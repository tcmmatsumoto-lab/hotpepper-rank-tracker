import os
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import urllib.parse

st.set_page_config(page_title="HotPepper順位トラッカー", layout="centered")
st.title("HotPepper 順位トラッカー")
st.caption("スプレッドシートの小エリアURLからキーワード検索を実行し、掲載順位を測定します。")

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


# 3. 高速・超軽量順位計測ロジック（requests版）
def check_rank_requests(raw_url, genre_key, target_kw, shop_target, search_pages, log_box):
    logs = []
    def add_log(msg):
        logs.append(msg)
        log_box.markdown("\n\n".join(logs))

    current_rank = 0
    found = False
    target_found_name = ""

    base_area_url = re.sub(r"/(nail|relax|este)/", f"/{genre_key}/", raw_url).rstrip("/") + "/"
    encoded_kw = urllib.parse.quote(target_kw)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    for page_idx in range(1, search_pages + 1):
        if page_idx == 1:
            target_url = f"{base_area_url}kw{encoded_kw}/"
        else:
            target_url = f"{base_area_url}kw{encoded_kw}/PN{page_idx}.html"

        add_log(f"🔍 **{page_idx}ページ目を検索中...**\n`{target_url}`")

        try:
            res = requests.get(target_url, headers=headers, timeout=15)
            if res.status_code != 200:
                add_log(f"⚠️ ステータスコード {res.status_code} で応答がありました。")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            
            links = soup.find_all("a", href=re.compile(r"slnH\d+"))

            page_shops = []
            seen_ids = set()

            for link in links:
                href = link.get("href", "")
                m_sln = re.search(r"(slnH\d+)", href)
                if not m_sln:
                    continue

                sln_id = m_sln.group(1)
                if sln_id in seen_ids:
                    continue

                clean_path = href.split("?")[0].rstrip("/")
                if any(clean_path.endswith(s) for s in ["/photo", "/coupon", "/map", "/review"]):
                    continue

                raw_text = link.get_text(strip=True)
                salon_name = raw_text.split("\n")[0].strip()

                if re.search(r"^\d+枚$", salon_name) or len(salon_name) <= 2:
                    continue
                if any(bad in salon_name for bad in ["空席確認", "予約する", "地図を見る", "クーポン一覧", "口コミ一覧"]):
                    continue

                seen_ids.add(sln_id)
                page_shops.append(salon_name)

            if not page_shops:
                add_log(f"⚠️ {page_idx}ページ目で店舗が見つかりませんでした。")
                break

            add_log(f"📄 **{len(page_shops)} 店舗**がヒットしました。判定中...")

            for name in page_shops:
                current_rank += 1
                add_log(f"{current_rank}位: **{name}**")

                if shop_target.lower() in name.lower():
                    found = True
                    target_found_name = name
                    break

            if found:
                break

        except Exception as e:
            add_log(f"❌ 通信エラー: {e}")
            break

    return current_rank, found, target_found_name


# 4. 実行ボタン
if st.button("順位を計測する", type="primary", key="ui_btn_measure"):
    if not shop_name or not keyword or not selected_url:
        st.error("自店舗名、検索キーワードを入力し、小エリアを選択してください。")
    else:
        st.write("---")
        st.subheader("計測ログ")
        log_placeholder = st.empty()

        with st.spinner("検索を実行して掲載順位を測定しています..."):
            rank, is_found, matched_name = check_rank_requests(
                selected_url, genre_prefix, keyword,
                shop_name, max_pages, log_placeholder
            )

        st.write("---")
        if is_found:
            st.balloons()
            st.success(f"🎉 対象店舗「{matched_name}」が見つかりました！")
            st.metric(label=f"「{selected_small_name}」×「{keyword}」の検索順位", value=f"{rank} 位")
        else:
            st.warning(f"指定された {max_pages} ページ以内（計 {rank} 店舗中）に「{shop_name}」は見つかりませんでした。")
