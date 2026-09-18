import os
import streamlit as st
import asyncio
import pandas as pd
import re
from playwright.async_api import async_playwright

# Streamlit Cloud環境用セットアップ
@st.cache_resource
def setup_playwright():
    try:
        os.system("playwright install chromium")
    except Exception:
        pass

setup_playwright()

st.set_page_config(page_title="HotPepper順位トラッカー", layout="centered")
st.title("HotPepper 順位トラッカー")
st.caption("小エリアURLからキーワード検索を実行し、正確な掲載店舗名のみを抽出して順位を判定します。")

# 1. Googleスプレッドシートの読み込み
SHEET_ID = "1HGmMHV4dEUQUy9VFEIc32erKUrhdOFUd4kYN-oEA4-c"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

@st.cache_data(ttl=60)
def load_area_data():
    try:
        df = pd.read_csv(CSV_URL)
        df = df.dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]

        large_cols = [c for c in df.columns if any(k in c for k in ["大エリア", "地方", "都道府県"])]
        mid_cols = [c for c in df.columns if "中エリア" in c]
        small_cols = [c for c in df.columns if "小エリア" in c and not any(k in c for k in ["URL", "url", "コード", "完全"])]
        url_cols = [c for c in df.columns if any(k in c for k in ["URL", "url", "完全URL"])]

        if not (large_cols and mid_cols and small_cols and url_cols):
            st.error(f"列名を認識できませんでした。検出列: {list(df.columns)}")
            return None

        df["_large"] = df[large_cols[0]].astype(str).str.strip()
        df["_mid"] = df[mid_cols[0]].astype(str).str.strip()
        df["_small"] = df[small_cols[0]].astype(str).str.strip()
        df["_url"] = df[url_cols[0]].astype(str).str.strip()

        df = df[df["_url"].str.startswith("http")].reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"スプレッドシート読み込みエラー: {e}")
        return None

df_areas = load_area_data()

GENRE_MAP = {
    "ネイル・まつげ": "nail",
    "リラクゼーション": "relax",
    "エステ": "este"
}

# 2. エリア選択UI
genre_name = st.selectbox("ジャンルを選択", list(GENRE_MAP.keys()), key="ui_genre_key")
genre_prefix = GENRE_MAP[genre_name]

selected_url = ""
selected_small_name = ""

if df_areas is not None and not df_areas.empty:
    col1, col2, col3 = st.columns(3)

    large_list = sorted(df_areas["_large"].dropna().unique().tolist())
    with col1:
        selected_large = st.selectbox("大エリア", large_list, key="sel_large_box")

    filtered_large = df_areas[df_areas["_large"] == selected_large]
    mid_list = sorted(filtered_large["_mid"].dropna().unique().tolist())
    with col2:
        selected_mid = st.selectbox("中エリア", mid_list, key=f"sel_mid_box_{selected_large}")

    filtered_mid = filtered_large[filtered_large["_mid"] == selected_mid]
    small_list = sorted(filtered_mid["_small"].dropna().unique().tolist())
    with col3:
        selected_small_name = st.selectbox("小エリア", small_list, key=f"sel_small_box_{selected_large}_{selected_mid}")

    matched_row = filtered_mid[filtered_mid["_small"] == selected_small_name]
    if not matched_row.empty:
        selected_url = matched_row["_url"].values[0]
        st.info(f"📌 開く小エリアURL: `{selected_url}`")
    else:
        st.warning("⚠️ エリアURLが見つかりませんでした。")
else:
    st.error("スプレッドシートから有効なエリアデータを取得できませんでした。")

col_shop, col_kw = st.columns(2)
with col_shop:
    shop_name = st.text_input("自店舗名（部分一致OK）", placeholder="", key="ui_shop_key")
with col_kw:
    keyword = st.text_input("検索キーワード", placeholder="", key="ui_kw_key")

max_pages = st.slider("調べるページ数（1ページ＝約20〜30店舗）", min_value=1, max_value=5, value=2, key="ui_max_pages")


# 3. 成功時ベースの検索実行＆サロン判定ロジック
async def check_rank(raw_url, genre_key, target_kw, shop_target, search_pages, log_box):
    logs = []
    def add_log(msg):
        logs.append(msg)
        log_box.markdown("\n\n".join(logs))

    async with async_playwright() as p:
        # Linuxコンテナ環境クラッシュ防止の必須起動オプション
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--mute-audio"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()
        await page.route("**/*.{png,jpg,jpeg,webp,svg,gif,woff,woff2}", lambda r: r.abort())

        # 1. 小エリアURLを開く
        target_area_url = re.sub(r"/(nail|relax|este)/", f"/{genre_key}/", raw_url).rstrip("/") + "/"
        add_log(f"1️⃣ **小エリアのURLを開いています...**\n`{target_area_url}`")

        try:
            await page.goto(target_area_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            add_log(f"❌ ページアクセス失敗: {e}")
            await browser.close()
            return 0, False, ""

        # 2. 検索窓口の特定
        search_input = None
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                inp_type = (await inp.get_attribute("type") or "text").lower()
                if inp_type in ["text", "search"]:
                    name_attr = (await inp.get_attribute("name") or "").lower()
                    id_attr = (await inp.get_attribute("id") or "").lower()
                    ph_attr = (await inp.get_attribute("placeholder") or "").lower()
                    if any(k in name_attr or k in id_attr or k in ph_attr for k in ["fw", "free", "word", "keyword", "サロン", "キーワード"]):
                        search_input = inp
                        break
            except Exception:
                continue

        if not search_input:
            for inp in inputs:
                try:
                    inp_type = (await inp.get_attribute("type") or "text").lower()
                    if inp_type in ["text", "search"] and await inp.is_visible():
                        search_input = inp
                        break
                except Exception:
                    continue

        if not search_input:
            add_log("❌ 検索窓が見つかりませんでした。")
            await browser.close()
            return 0, False, ""

        # 3. 入力して検索を実行
        await search_input.scroll_into_view_if_needed()
        await search_input.click()
        await search_input.fill("")
        await search_input.fill(target_kw)
        add_log(f"2️⃣ **検索ウィンドウに「{target_kw}」を入力しました。**")
        await asyncio.sleep(0.5)

        add_log("3️⃣ **検索ボタンを押して検索を実行します...**")
        submitted = False
        btn_candidates = await page.query_selector_all("button, input[type='submit'], a.btnSearch, a.searchBtn")
        for btn in btn_candidates:
            try:
                txt = (await btn.inner_text() or await btn.get_attribute("value") or "").strip()
                if "検索" in txt and await btn.is_visible():
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=25000):
                        await btn.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=25000):
                    await search_input.press("Enter")
                submitted = True
            except Exception:
                pass

        await asyncio.sleep(2)
        add_log(f"✅ **検索結果ページが表示されました:**\n`{page.url}`")

        current_rank = 0
        found = False
        target_found_name = ""

        # 4. サロン一覧の抽出（サロンIDごとに最上部の見出しのみを厳密採用）
        for page_idx in range(1, search_pages + 1):
            if page_idx > 1:
                cur_url = page.url
                if "/PN" in cur_url:
                    next_url = re.sub(r"/PN\d+(\.html)?", f"/PN{page_idx}.html", cur_url)
                elif "?" in cur_url:
                    parts = cur_url.split("?")
                    next_url = f"{parts[0].rstrip('/')}/PN{page_idx}.html?{parts[1]}"
                else:
                    next_url = f"{cur_url.rstrip('/')}/PN{page_idx}.html"

                add_log(f"\n🔍 **{page_idx}ページ目をスキャン中...** (`{next_url}`)")
                await page.goto(next_url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(2)

            # ページ内のサロン個別トップリンク（/slnH.../）を走査
            links = await page.query_selector_all("a[href*='/kr/slnH']")

            page_shops = []
            seen_ids = set()

            for link in links:
                href = await link.get_attribute("href") or ""
                
                # サロン個別トップ（/slnH.../）のみを対象にし、写真・クーポン等のサブページは除外
                m_sln = re.search(r"/(slnH\d+)/?$", href.split("?")[0])
                if not m_sln:
                    continue

                sln_id = m_sln.group(1)
                if sln_id in seen_ids:
                    continue

                raw_text = (await link.inner_text()).strip()
                salon_name = raw_text.split("\n")[0].strip()

                # 写真枚数（〇枚）や短すぎるテキストを除外
                if re.search(r"^\d+枚$", salon_name) or len(salon_name) <= 2:
                    continue
                # ボタン文言等を除外
                if any(bad in salon_name for bad in ["空席確認", "予約する", "地図を見る", "クーポン一覧"]):
                    continue

                seen_ids.add(sln_id)
                page_shops.append(salon_name)

            if not page_shops:
                add_log(f"⚠️ {page_idx}ページ目で店舗が検出されませんでした。")
                break

            add_log(f"📄 **{len(page_shops)} 店舗**がヒットしました。判定中...")

            for name in page_shops:
                current_rank += 1
                add_log(f"{current_rank}位: **{name}**")

                # 部分一致判定（大文字・小文字を無視）
                if shop_target.lower() in name.lower():
                    found = True
                    target_found_name = name
                    break

            if found:
                break

        await browser.close()
        return current_rank, found, target_found_name


# 4. 実行ボタン
if st.button("順位を計測する", type="primary", key="ui_btn_measure"):
    if not shop_name or not keyword or not selected_url:
        st.error("自店舗名、検索キーワードを入力し、小エリアを選択してください。")
    else:
        st.write("---")
        st.subheader("計測ログ")
        log_placeholder = st.empty()

        with st.spinner("キーワード検索を実行して正確な順位を測定しています..."):
            rank, is_found, matched_name = asyncio.run(
                check_rank(
                    selected_url, genre_prefix, keyword,
                    shop_name, max_pages, log_placeholder
                )
            )

        st.write("---")
        if is_found:
            st.balloons()
            st.success(f"🎉 対象店舗「{matched_name}」が見つかりました！")
            st.metric(label=f"「{selected_small_name}」×「{keyword}」の検索順位", value=f"{rank} 位")
        else:
            st.warning(f"指定された {max_pages} ページ以内（計 {rank} 店舗中）に「{shop_name}」は見つかりませんでした。")
            