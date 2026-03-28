import asyncio
import urllib.parse
import aiohttp
import re
import time
import pandas as pd
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

MAX_WORKERS = 10
api_extra_info = {}


async def handle_response(response):
    if "u-search/exactmatch" in response.url:
        try:
            res = await response.json()
            products = res.get("products", [])
            for p in products:
                id_vovanchika = str(p.get("id"))

                # Собираем размеры
                sz = [str(s.get("name", "")) for s in p.get("sizes", [])]

                supp_id = p.get("supplierId")

                api_extra_info[id_vovanchika] = {
                    "Название": p.get("name"),
                    "Бренд": p.get("brand"),
                    "Цена": p.get("sizes")[0].get("price", {}).get("product", 0) / 100 if p.get("sizes") else 0,
                    "Рейтинг": p.get("reviewRating"),
                    "Отзывов": p.get("feedbacks"),
                    "Название селлера": p.get("supplier"),
                    "Ссылка на селлера": f"https://www.wildberries.ru/seller/{supp_id}" if supp_id else "",
                    "Размеры": ", ".join(sz),
                    "Остатки": p.get("totalQuantity", 0)
                }
        except Exception:
            pass


async def get_card_info(session, url):
    # Парсим саму карточку через json (баскеты)
    link = f"{url}/info/ru/card.json"
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.wildberries.ru/"}

    try:
        async with session.get(link, headers=h, timeout=10) as r:
            if r.status == 200:
                json_body = await r.json()
                text = json_body.get('description', '-')
                opts = json_body.get('options', [])

                # Собираем характеристики в одну кучу
                s_list = []
                for o in opts:
                    s_list.append(f"{o['name']}: {o['value']}")
                all_specs = "\n".join(s_list)


                cnt = json_body.get('media', {}).get('photo_count', 1)
                all_pics = [f"{url}/images/big/{i}.webp" for i in range(1, cnt + 1)]

                return text, all_specs, ", ".join(all_pics)
    except:
        # print("Ошибка в get_card_info для " + url)
        pass
    return "нет", "нет",  "пусто"


async def scroll_and_load_cards(page):
    # Скроллит страницу, пока количество карточек не совпадет с найденным числом
    try:
        await page.wait_for_selector('span.searching-results__count', timeout=10000)
        total_text = await page.locator('span.searching-results__count').inner_text()
        total_expected = int(re.sub(r'\D', '', total_text))
        print(f"Цель: найти {total_expected} товаров")
    except:
        total_expected = 50
        print("Счетчик не найден, скроллим до минимума...")

    current_count = 0
    last_increase_time = time.time()

    while True:
        new_count = await page.locator('.product-card-list article').count()

        if new_count >= total_expected:
            print(f"Готово! Найдено карточек: {new_count}")
            break

        if new_count > current_count:
            print(f"Прогресс скролла: {new_count} / {total_expected}")
            current_count = new_count
            last_increase_time = time.time()
        else:
            if time.time() - last_increase_time > 60:
                print(f"Таймаут: карточки не подгружаются. Останавливаемся на {current_count}.")
                break
            await page.keyboard.press("PageUp")
            await asyncio.sleep(0.5)

        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await page.keyboard.press("PageDown")
        await asyncio.sleep(0.8)


async def work_with_item(session, itm, sem):
    # Ограничиваем количество одновременных запросов
    async with sem:
        id_nm = itm['nm_id']
        info = api_extra_info.get(id_nm, {})

        d, s, p = await get_card_info(session, itm['base_url'])

        # Возвращаем готовую строку для таблицы
        return {
            "Ссылка": f"https://www.wildberries.ru/catalog/{id_nm}/detail.aspx",
            "Артикул": id_nm,
            "Название": info.get("Название", "-"),
            "Бренд": info.get("Бренд", "-"),
            "Цена": info.get("Цена", 0),
            "Описание": d,
            "Картинки": p,
            "Характеристики": s,
            "Селлер": info.get("Название селлера", ""),
            "Магазин": info.get("Ссылка на селлера", ""),
            "Размеры": info.get("Размеры", ""),
            "На складе": info.get("Остатки", 0),
            "Звезды": info.get("Рейтинг", 0),
            "Отзывы": info.get("Отзывов", 0)
        }


async def main():
    q = "пальто из натуральной шерсти"
    # q = "носки мужские"
    enc_q = urllib.parse.quote(q)
    url = f"https://www.wildberries.ru/catalog/0/search.aspx?search={enc_q}"
    #url = f"https://www.wildberries.ru/catalog/0/search.aspx?page=1&sort=popular&search={enc_q}&priceU=56500%3B1000000&f14177451=15000203&meta_charcs=false"

    async with Stealth().use_async(async_playwright()) as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={'width': 1600, 'height': 900})
        page = await ctx.new_page()

        page.on("response", handle_response)

        await page.goto(url, wait_until="domcontentloaded")
        await scroll_and_load_cards(page)

        data_from_page = await page.evaluate('''() => {
            const res = [];
            const cards = document.querySelectorAll('.product-card-list article');
            cards.forEach(c => {
                const id = c.getAttribute('data-nm-id');
                const img = c.querySelector('img.j-thumbnail');
                let src = img ? (img.getAttribute('src') || img.getAttribute('data-src-pb')) : '';
                if (id && src) {
                    if (src.startsWith('//')) src = 'https:' + src;
                    res.push({ nm_id: id, base_url: src.split('/images/')[0] });
                }
            });
            return res;
        }''')
        await browser.close()

    # Чистим дубли на всякий случай
    clean_list = []
    seen = set()
    for x in data_from_page:
        if x['nm_id'] not in seen:
            clean_list.append(x)
            seen.add(x['nm_id'])

    print(f"Нашли {len(clean_list)} товаров. Начинаем детальный сбор...")

    sm = asyncio.Semaphore(MAX_WORKERS)
    async with aiohttp.ClientSession() as s:
        all_tasks = []
        for i in clean_list:
            all_tasks.append(work_with_item(s, i, sm))

        final_data = await asyncio.gather(*all_tasks)

    result_df = pd.DataFrame(final_data)

    # Чтобы артикул был числом
    result_df['Артикул'] = pd.to_numeric(result_df['Артикул'], errors='coerce')
    result_df['Цена'] = pd.to_numeric(result_df['Цена'], errors='coerce')

    file_name = "results_wb1.xlsx"
    result_df.to_excel(file_name, index=False)
    print(f"Сохранил в {file_name}")


if __name__ == "__main__":
    asyncio.run(main())