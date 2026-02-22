import os
import html
import unicodedata

import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="皮膚科 症状説明アプリ",
    page_icon="🩺",
    layout="wide"
)

# -------------------------
# よく使う病名（院内で固定したいトップ）
# ★ここを院内の実態に合わせて編集する
# -------------------------
FREQUENT_NAMES = [
    "アトピー性皮膚炎",
    "じんましん",
    "湿疹・皮膚炎（一般）",
    "手湿疹",
    "乾燥肌、皮脂欠乏性湿疹",
    "異汗性湿疹、汗疱（手足の水ぶくれ）",
]

MAX_FREQUENT_BUTTONS = 10  # 画面に出す上限（多すぎると見にくい）

# -------------------------
# CSS（見切れ対策 + 医療アプリっぽいカード）
# -------------------------
st.markdown("""
<style>
.block-container {
  padding-top: 3.5rem;
  padding-bottom: 2rem;
  max-width: 1200px;
  padding-left: 1.2rem;
  padding-right: 1.2rem;
}

h1, h2 { white-space: normal !important; overflow-wrap: anywhere !important; word-break: break-word !important; }
h1 { font-size: 1.45rem !important; line-height: 1.35 !important; margin: 0 0 0.4rem 0 !important; }

.small-muted { color: rgba(49, 51, 63, 0.65); font-size: 0.95rem; }

.card {
  border: 1px solid rgba(49, 51, 63, 0.10);
  border-radius: 16px;
  padding: 14px 14px;
  background: rgba(255,255,255,0.85);
  box-shadow: 0 2px 10px rgba(0,0,0,0.04);
  margin: 10px 0;
}
.card-title { font-weight: 800; font-size: 1.05rem; margin-bottom: 6px; }
.card-sub { color: rgba(49, 51, 63, 0.78); font-size: 0.95rem; line-height: 1.6; }

.section-title { font-weight: 800; margin-top: 14px; margin-bottom: 8px; font-size: 1.05rem; }

.note {
  border-left: 4px solid rgba(49, 51, 63, 0.18);
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(49, 51, 63, 0.03);
  color: rgba(49, 51, 63, 0.78);
  font-size: 0.92rem;
}

.stButton button {
  border-radius: 12px !important;
  padding: 0.55rem 0.85rem !important;
  border: 1px solid rgba(49, 51, 63, 0.18) !important;
}

/* 頻出ボタンを横並びにしやすくする */
.freq-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
}
.freq-label {
  font-size: 0.95rem;
  color: rgba(49, 51, 63, 0.75);
  margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)

# -------------------------
# 日本語正規化：全角/半角統一 + ひらがな/カタカナ統一
# -------------------------
def hira_to_kata(s: str) -> str:
    res = []
    for ch in s:
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:
            res.append(chr(code + 0x60))
        else:
            res.append(ch)
    return "".join(res)

def normalize_jp(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.strip().lower()
    s = hira_to_kata(s)
    return s

# -------------------------
# aliases の分割（; または \\n または 改行でOK）
# -------------------------
def split_aliases(text: str) -> list[str]:
    if text is None:
        return []
    s = str(text).strip()
    if not s:
        return []
    s = s.replace("\\n", "\n").replace("；", ";").replace(";", "\n")
    return [x.strip() for x in s.split("\n") if x.strip()]

# -------------------------
# サジェスト生成（aliases対応）
# 完全一致 > 前方一致 > 部分一致（最大8件）
# ・name だけでなく aliases も検索対象
# ・候補表示は「正式名（name）」を返す
# -------------------------
def build_suggestions_with_aliases(query: str, name_to_targets: dict[str, list[str]], limit: int = 8) -> list[str]:
    qn = normalize_jp(query)
    if not qn:
        return []

    scored: list[tuple[int, int, str, str]] = []
    for display_name, targets in name_to_targets.items():
        best_rank = None
        best_len = None
        best_sort_key = None

        for t in targets:
            tn = normalize_jp(t)
            if not tn:
                continue

            if tn == qn:
                rank = 0
            elif tn.startswith(qn):
                rank = 1
            elif qn in tn:
                rank = 2
            else:
                continue

            if best_rank is None or rank < best_rank or (rank == best_rank and len(tn) < (best_len or 10**9)):
                best_rank = rank
                best_len = len(tn)
                best_sort_key = tn

            if best_rank == 0:
                break

        if best_rank is not None:
            scored.append((best_rank, best_len or 10**9, best_sort_key or "", display_name))

    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[3] for x in scored[:limit]]

def find_exact_name_with_aliases(query: str, name_to_targets: dict[str, list[str]]) -> str | None:
    qn = normalize_jp(query)
    if not qn:
        return None
    for display_name, targets in name_to_targets.items():
        for t in targets:
            if normalize_jp(t) == qn:
                return display_name
    return None

# -------------------------
# CSVパス（data.csv / date.csv のどっちでもOK）
# -------------------------
def pick_csv_path() -> str:
    if os.path.exists("data.csv"):
        return "data.csv"
    if os.path.exists("date.csv"):
        return "date.csv"
    return ""

CSV_PATH = pick_csv_path()
if not CSV_PATH:
    st.error("data.csv も date.csv も見つかりません。app.py と同じフォルダに置いてください。")
    st.stop()

# -------------------------
# CSV読み込み（更新検知つきキャッシュ）
# ★overview は病名ごとに1回だけ書けばOK（空欄は自動補完）
# ★aliases 列があれば検索対象に含める（なくてもOK）
# -------------------------
@st.cache_data
def load_csv(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = df.columns.str.strip()
    df = df.fillna("")

    base_cols = ["name", "overview", "treatment_title", "pros", "cons", "cost"]
    opt_cols = ["aliases"]
    for col in base_cols + opt_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # overview補完（同じnameで1回だけ書けばOK）
    if "name" in df.columns and "overview" in df.columns:
        df["overview"] = df["overview"].astype(str).str.strip()
        df.loc[df["overview"] == "", "overview"] = pd.NA
        df["overview"] = df.groupby("name")["overview"].transform(lambda s: s.ffill().bfill())
        df["overview"] = df["overview"].fillna("")

    return df

try:
    mtime = os.path.getmtime(CSV_PATH)
    df = load_csv(CSV_PATH, mtime)
except Exception as e:
    st.error("CSVの読み込みに失敗しました。")
    st.code(str(e))
    st.stop()

required = ["name", "overview", "treatment_title", "pros", "cons", "cost"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error(f"CSVの列名が足りません: {missing}")
    st.write("読み取れた列名:", list(df.columns))
    st.info("1行目（見出し）はこれにしてください： name,overview,treatment_title,pros,cons,cost（aliases は任意）")
    st.stop()

# -------------------------
# 箇条書き表示：; または \\n または 改行でOK
# -------------------------
def split_items(text: str) -> list[str]:
    if text is None:
        return []
    s = str(text).strip()
    if not s:
        return []
    s = s.replace("\\n", "\n").replace("；", ";").replace(";", "\n")
    return [x.strip() for x in s.split("\n") if x.strip()]

def ul_html(items: list[str]) -> str:
    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    return f"<ul style='margin:6px 0 0 20px; padding:0; line-height:1.6;'>{lis}</ul>"

# -------------------------
# 状態
# -------------------------
if "page" not in st.session_state:
    st.session_state.page = "home"
if "selected_name" not in st.session_state:
    st.session_state.selected_name = ""
if "query_text" not in st.session_state:
    st.session_state.query_text = ""

# -------------------------
# ヘッダー
# -------------------------
def render_header():
    st.markdown("# 🩺 皮膚科 症状説明アプリ")
    st.markdown(
        f'<div class="small-muted">読み込み中：<b>{html.escape(CSV_PATH)}</b>（CSVを保存すると自動で反映されます）</div>',
        unsafe_allow_html=True
    )
    st.write("")

# -------------------------
# 詳細ページ
# -------------------------
def render_detail(name: str):
    render_header()

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← 戻る"):
            st.session_state.page = "home"
            st.session_state.selected_name = ""
            st.rerun()
    with col2:
        st.write("")

    g = df[df["name"] == name]
    if g.empty:
        st.warning("データが見つかりませんでした。CSVの病名（name列）に余計な空白がないか確認してください。")
        return

    st.markdown(f"""
    <div class="card">
      <div class="card-title">{html.escape(name)}</div>
      <div class="card-sub">説明用の要点をまとめています（必要に応じて医師が補足してください）。</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-title">どんな病気？</div>', unsafe_allow_html=True)
    overview = str(g["overview"].iloc[0])
    st.markdown(
        f'<div class="card"><div class="card-sub">{html.escape(overview)}</div></div>',
        unsafe_allow_html=True
    )

    st.markdown('<div class="section-title">治療法（メリット・デメリット・費用）</div>', unsafe_allow_html=True)

    for _, row in g.iterrows():
        title = str(row["treatment_title"])
        pros_items = split_items(row["pros"])
        cons_items = split_items(row["cons"])
        cost = str(row["cost"]).strip()

        pros_block = ul_html(pros_items) if pros_items else "<div style='margin-top:6px;'>（記載なし）</div>"
        cons_block = ul_html(cons_items) if cons_items else "<div style='margin-top:6px;'>（記載なし）</div>"

        st.markdown(f"""
        <div class="card">
          <div class="card-title">{html.escape(title)}</div>
          <div class="card-sub"><b>メリット：</b>{pros_block}</div>
          <div class="card-sub" style="margin-top:8px;"><b>デメリット：</b>{cons_block}</div>
          <div class="card-sub" style="margin-top:8px;"><b>費用目安：</b>{html.escape(cost) if cost else "（記載なし）"}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""
    <div class="note">
      ※ このアプリは説明支援の学習用サンプルです。実運用では医療機関の監修、表現の統一、患者さんの状況に応じた説明が必要です。
    </div>
    """, unsafe_allow_html=True)

# -------------------------
# ホーム（頻出ボタン + サジェスト検索）
# -------------------------
def render_home():
    render_header()

    # 病名（正式名）
    all_names = sorted(df["name"].astype(str).unique())

    # 病名→検索対象（正式名 + aliases）
    has_aliases = "aliases" in df.columns
    name_to_targets: dict[str, list[str]] = {}
    for name in all_names:
        targets = [name]
        if has_aliases:
            alias_series = df.loc[df["name"] == name, "aliases"]
            alias_list: list[str] = []
            for a in alias_series.astype(str).tolist():
                alias_list.extend(split_aliases(a))

            seen = set()
            uniq_aliases = []
            for a in alias_list:
                key = normalize_jp(a)
                if key and key not in seen:
                    seen.add(key)
                    uniq_aliases.append(a)
            targets.extend(uniq_aliases)
        name_to_targets[name] = targets

    # -------------------------
    # よく使う病名ボタン（院内時短）
    # -------------------------
    # CSVに存在するものだけ表示（存在しない病名は自動で除外）
    freq_candidates = [n for n in FREQUENT_NAMES if n in set(all_names)]
    freq_candidates = freq_candidates[:MAX_FREQUENT_BUTTONS]

    if freq_candidates:
        st.markdown('<div class="section-title">よく使う病名（クリックで詳細へ）</div>', unsafe_allow_html=True)

        # 4列くらいで並べる（診察室で見やすい）
        cols = st.columns(4)
        for i, name in enumerate(freq_candidates):
            with cols[i % 4]:
                if st.button(f"⭐ {name}", key=f"freq_{i}_{name}"):
                    st.session_state.page = "detail"
                    st.session_state.selected_name = name
                    st.rerun()

    # -------------------------
    # 検索（サジェスト）
    # -------------------------
    with st.form("search_form", clear_on_submit=False):
        query = st.text_input(
            "病名を検索（1文字目からサジェスト / 別名もOK）",
            value=st.session_state.query_text
        )
        submitted = st.form_submit_button("検索")

    st.session_state.query_text = query

    if len(query.strip()) >= 1:
        suggestions = build_suggestions_with_aliases(query, name_to_targets, limit=8)

        if suggestions:
            st.markdown('<div class="section-title">サジェスト（クリックで詳細へ）</div>', unsafe_allow_html=True)
            for i, name in enumerate(suggestions):
                if st.button(f"▶ {name}", key=f"sug_{i}_{name}"):
                    st.session_state.page = "detail"
                    st.session_state.selected_name = name
                    st.rerun()
        else:
            st.info("候補がありません。")

    if submitted:
        exact = find_exact_name_with_aliases(query, name_to_targets)
        if exact:
            st.session_state.page = "detail"
            st.session_state.selected_name = exact
            st.rerun()
        else:
            st.info("完全一致がありません。上のサジェストから選んでください。")

# -------------------------
# ルーティング
# -------------------------
if st.session_state.page == "detail" and st.session_state.selected_name:
    render_detail(st.session_state.selected_name)
else:
    render_home()