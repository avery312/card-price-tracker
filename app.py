import streamlit as st
import pandas as pd
# 明确导入 datetime 和 date 对象
from datetime import datetime, date 
import requests
from bs4 import BeautifulSoup
import re 
import numpy as np 
# 导入 Supabase 客户端库
from supabase import create_client, Client 
import time 
# 引入 components 用于执行 JavaScript 滚动
import streamlit.components.v1 as components

# === 配置 ===
SUPABASE_TABLE_NAME = "cards" 
NEW_EXPECTED_COLUMNS = ['id', 'date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
if 'form_key_suffix' not in st.session_state: 
    st.session_state['form_key_suffix'] = 0

if 'submission_successful' not in st.session_state: 
    st.session_state['submission_successful'] = False
if 'submitted_card_name' not in st.session_state: 
    st.session_state['submitted_card_name'] = "" 

if 'last_entry_date' not in st.session_state:
    st.session_state['last_entry_date'] = datetime.now().date() 
    
if 'autosave_successful' not in st.session_state:
    st.session_state['autosave_successful'] = False
if 'autosave_message' not in st.session_state:
    st.session_state['autosave_message'] = ""
    
if 'date_range_input' not in st.session_state:
    st.session_state['date_range_input'] = [] 
if 'search_name_input' not in st.session_state:
    st.session_state['search_name_input'] = ""
if 'search_set_input' not in st.session_state:
    st.session_state['search_set_input'] = ""


def clear_all_data():
    """清除所有录入相关 Session State。"""
    st.session_state['scrape_result'] = {} 
    st.session_state['form_key_suffix'] += 1 
    st.session_state['last_entry_date'] = datetime.now().date() 

def clear_search_filters_action():
    """清除所有筛选相关的 Session State 变量。用于 on_click 回调。"""
    st.session_state["search_name_input"] = ""
    st.session_state["search_set_input"] = ""
    st.session_state["date_range_input"] = [] 


# === 辅助函数：模糊搜索规范化 ===
def normalize_text_for_fuzzy_search(text):
    if pd.isna(text):
        return ""
    cleaned = str(text).replace('-', '').replace(' ', '')
    return cleaned.upper()

# 辅助函数：检查卡牌是否存在 (基于 card_number)
def check_card_exists(card_number, unique_cards_df):
    """Check if a card number already exists and return its latest details."""
    if card_number and card_number in unique_cards_df['card_number'].values:
        return unique_cards_df[unique_cards_df['card_number'] == card_number].iloc[0].to_dict()
    return None

# === Supabase 数据库函数 ===

@st.cache_resource(ttl=None)
def connect_supabase() -> Client:
    """使用 Streamlit Secrets 凭证连接到 Supabase 数据库 (连接对象缓存)"""
    try:
        url: str = st.secrets["supabase"]["URL"]
        key: str = st.secrets["supabase"]["KEY"]
        supabase: Client = create_client(url, key)
        return supabase
    except Exception as e:
        st.error(f"无法连接 Supabase 数据库。请检查 secrets.toml 配置。错误: {e}")
        return None

def load_data():
    """从 Supabase 读取所有数据 (每次脚本运行时都强制读取)"""
    supabase = connect_supabase()
    if not supabase:
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
    
    try:
        # 直接读取数据
        response = supabase.table(SUPABASE_TABLE_NAME).select("*").order("date", desc=True).execute()
        
        df = pd.DataFrame(response.data)
        
        if df.empty:
             return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

        df = df.replace({np.nan: None}) 
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        df['date_dt'] = pd.to_datetime(df['date'], errors='coerce')
        df = df[NEW_EXPECTED_COLUMNS + ['date_dt']] 

        return df
    except Exception as e:
        st.error(f"无法从 Supabase 读取数据。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS + ['date_dt'])

# 新增/追加卡牌
def add_card(name, number, card_set, price, quantity, rarity, color, date, image_url=None):
    supabase = connect_supabase()
    if not supabase: return
    
    try:
        # 1. 直接查询 Supabase 获取最大的 ID 
        response = supabase.table(SUPABASE_TABLE_NAME).select("id").order("id", desc=True).limit(1).execute()
        
        max_id = 0
        if response.data and response.data[0] and 'id' in response.data[0]:
            # 找到当前最大的 ID
            max_id = response.data[0]['id']
            
        new_id = int(max_id + 1) if pd.notna(max_id) else 1
        
        # 2. 准备要插入的字典数据
        new_row_data = {
            "id": new_id,
            "date": date.strftime('%Y-%m-%d'),
            "card_number": number,
            "card_name": name,
            "card_set": card_set,
            "price": price,
            "quantity": quantity,
            "rarity": rarity,
            "color": color,
            "image_url": image_url if image_url else ""
        }
        
        # 3. 执行插入操作
        supabase.table(SUPABASE_TABLE_NAME).insert(new_row_data).execute()
        
    except Exception as e:
        st.error(f"追加数据到 Supabase 失败。错误: {e}")

# 增量保存函数，用于自动保存
def save_incremental_changes(displayed_df: pd.DataFrame, editor_state: dict):
    """
    根据 data_editor 的状态，对 Supabase 进行精确的 UPSERT 和 DELETE 操作。
    """
    supabase = connect_supabase()
    if not supabase: return
    
    deleted_count = 0
    updated_count = 0
    
    try:
        # 1. 处理删除操作 (DELETE)
        deleted_indices = editor_state.get("deleted_rows", [])
        if deleted_indices:
            # 过滤无效索引
            valid_indices = [i for i in deleted_indices if i < len(displayed_df)]
            ids_to_delete = displayed_df.iloc[valid_indices]['id'].tolist()
            
            if ids_to_delete:
                deleted_count = len(ids_to_delete)
                supabase.table(SUPABASE_TABLE_NAME).delete().in_('id', ids_to_delete).execute()

        # 2. 处理修改操作 (UPSERT/UPDATE)
        edited_rows = editor_state.get("edited_rows", {})
        if edited_rows:
            data_to_upsert = []
            
            for filtered_index, changes in edited_rows.items():
                if filtered_index in deleted_indices:
                    continue
                
                if filtered_index >= len(displayed_df):
                    continue
                    
                row_id = displayed_df.iloc[filtered_index]['id']
                update_data = {'id': int(row_id)}
                
                # 获取原始日期并设置回退值
                original_date_ts = displayed_df.iloc[filtered_index]['date']
                initial_date_str = datetime.now().strftime('%Y-%m-%d')
                if original_date_ts:
                     initial_date_str = str(original_date_ts)

                update_data['date'] = initial_date_str 
                
                for col, value in changes.items():
                    if col == 'date':
                        if value:
                             update_data[col] = value
                    elif col in ['price']:
                        update_data[col] = float(value) if pd.notna(value) else 0.0
                    elif col in ['quantity']:
                        update_data[col] = int(value) if pd.notna(value) else 0
                    else:
                        update_data[col] = str(value) if pd.notna(value) else ""
                        
                data_to_upsert.append(update_data)
            
            if data_to_upsert:
                updated_count = len(data_to_upsert)
                supabase.table(SUPABASE_TABLE_NAME).upsert(data_to_upsert).execute()

        if deleted_count > 0 or updated_count > 0:
            msg = f"✅ 已自动保存：更新 {updated_count} 条，删除 {deleted_count} 条。"
            st.session_state['autosave_successful'] = True
            st.session_state['autosave_message'] = msg
        
    except Exception as e:
        st.session_state['autosave_successful'] = True
        st.session_state['autosave_message'] = f"❌ 自动保存失败。错误: {e}"


# === 核心修改：基于规则的抓取函数 ===
def scrape_card_data(url):
    st.info(f"正在尝试从 {url} 抓取数据...")
    if not url.startswith("http"):
        return {"error": "网址格式不正确。"}
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status() 
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.content, 'html.parser')

        # 1. 获取标题
        name_tag = soup.find(['h1', 'h2'], class_=re.compile(r'heading|title', re.I))
        full_title = name_tag.get_text(strip=True) if name_tag else ""
        
        if not full_title:
             return {"error": "未能找到卡牌名称标题。"}

        # 初始化变量
        card_name = "N/A"
        rarity = ""
        color = ""
        card_number = ""
        card_set = "" 
        
        text = full_title # 工作副本

        # 2. 提取 Rarity 【...】
        # 提取第一个 【】 内容作为稀有度，并从文本中移除
        r_match = re.search(r'【(.+?)】', text)
        if r_match:
            rarity = r_match.group(1).strip()
            text = text.replace(r_match.group(0), ' ').strip()
        
        # 3. 提取 Color 《...》
        # 提取第一个 《》 内容作为颜色，并从文本中移除
        c_match = re.search(r'《(.+?)》', text)
        if c_match:
            color = c_match.group(1).strip()
            text = text.replace(c_match.group(0), ' ').strip()
            
        # 4. 提取末尾的 [...] 信息 (包含 Series 和 Number)
        # 查找最后一个 [...] 块
        b_match = re.search(r'\[([^\]]+)\]\s*$', text)
        if b_match:
            bracket_content = b_match.group(1).strip()
            # 从主文本中移除这部分
            text = text.replace(b_match.group(0), ' ').strip()
            
            # --- 解析 [...] 内部 ---
            
            # 检查是否存在 『...』 (例如 [SPOP07-001『EB02』])
            set_in_bracket_match = re.search(r'『(.+?)』', bracket_content)
            
            if set_in_bracket_match:
                # 规则 2：有『』时，『』内是系列，剩下的是编号
                card_set = set_in_bracket_match.group(1).strip()
                # 移除系列，剩下的就是编号
                card_number = bracket_content.replace(set_in_bracket_match.group(0), '').strip()
            else:
                # 规则 1：无『』时，例如 [【1st ANNIVERSARY SET】版OP01-006]
                # 尝试找到末尾的编号 (格式通常是 字母+数字-数字)
                # 正则：[A-Za-z0-9]+-\d+ (例如 OP01-006)
                num_match = re.search(r'([A-Za-z0-9]+-\d+)\s*$', bracket_content)
                if num_match:
                    card_number = num_match.group(1).strip()
                    # 移除编号，剩下的就是系列
                    # 注意：这里可能会剩下 【...】版，这是系列名的一部分
                    card_set = bracket_content[:num_match.start()].strip()
                else:
                    # 兜底：如果找不到明显的编号格式，整个作为系列？或者根据实际情况调整
                    card_set = bracket_content
        
        # 5. 提取 Name
        # 经过上述移除后，剩下的部分就是卡名
        card_name = text.strip()

        # 6. 提取图片链接
        image_url = None
        og_image_tag = soup.find('meta', property='og:image')
        if og_image_tag:
            image_url = og_image_tag.get('content')
        if not image_url:
            image_tag = soup.find('img', {'alt': lambda x: x and 'メイン画像' in x}) or \
                        soup.find('img', {'alt': lambda x: x and card_name in x})
            if image_tag:
                image_url = image_tag.get('data-src') or image_tag.get('src') 
        
        if not image_url:
            st.warning("未能找到图片链接。")

        return {
            "card_name": card_name, "card_number": card_number, "card_set": card_set,
            "card_rarity": rarity, "card_color": color, "image_url": image_url, "error": None
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"网络错误或无法访问: {e}"}
    except Exception as e:
        return {"error": f"解析错误: {e}"}

# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")

suffix = str(st.session_state['form_key_suffix']) 

# 🔑 load_data() 每次 rerun 都会执行数据库读取
df = load_data() 

# --- NEW: Get unique card definitions for lookups and selection ---
if not df.empty:
    df['image_url'] = df['image_url'].fillna('')
    df['rarity'] = df['rarity'].fillna('') 
    df['color'] = df['color'].fillna('') 
    df['card_set'] = df['card_set'].fillna('') 
    df['card_number'] = df['card_number'].fillna('') 
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int) 
    df = df.dropna(subset=['date_dt']) 

    unique_cards_df = df.sort_values('date_dt', ascending=False).drop_duplicates(subset=['card_number'], keep='first')
    unique_cards_df['display_label'] = unique_cards_df.apply(
        lambda x: f"[{x['card_number']}] {x['card_name']} ({x['card_set']})", axis=1
    )
    st.session_state['unique_cards'] = unique_cards_df[['card_number', 'card_name', 'card_set', 'rarity', 'color', 'image_url', 'display_label']]
    card_options = {label: num for label, num in zip(st.session_state['unique_cards']['display_label'], st.session_state['unique_cards']['card_number'])}
else:
    if 'unique_cards' not in st.session_state:
        st.session_state['unique_cards'] = pd.DataFrame(columns=['card_number', 'card_name', 'card_set', 'rarity', 'color', 'image_url', 'display_label'])
    card_options = {}

# --- 侧边栏：录入 ---
with st.sidebar:
    
    # 【侧边栏滚动修复】：当提交成功后，执行 JS 滚动，并显示成功消息
    if st.session_state.get('submission_successful'):
        card_name = st.session_state.get('submitted_card_name', '一张卡牌')
        st.success(f"✅ **{card_name}** 录入成功！", icon="🎉") 
        components.html('<script>window.parent.scrollTo(0,0);</script>', height=0)
    
    st.header("🌐 网页自动填充")
    scrape_url = st.text_input("输入卡牌详情页网址:", key=f'scrape_url_input_{suffix}') 
    
    col_scrape_btn, col_clear_btn = st.columns(2)
    
    with col_scrape_btn:
        if st.button("一键抓取并填充", type="secondary", key=f"scrape_btn_{suffix}"):
            if not scrape_url: st.warning("请输入网址。")
            else:
                st.session_state['scrape_result'] = scrape_card_data(scrape_url)
                if st.session_state['scrape_result'].get('error'): 
                    st.error(st.session_state['scrape_result']['error'])
                else: 
                    st.success("数据抓取完成。")
                st.session_state['form_key_suffix'] += 1
                st.rerun() 
                 
    with col_clear_btn:
        if st.button("一键清除录入内容", type="primary", key=f"clear_btn_{suffix}", on_click=clear_all_data):
            st.rerun() 

    st.divider()
    st.header("📝 录入新卡/更新价格")
    
    # --- STEP 1: Card Identification (by Number or Selection) ---
    selected_label = st.selectbox(
        "选择已有的卡牌进行价格更新：",
        options=[''] + list(card_options.keys()),
        index=0,
        key=f"card_select_{suffix}"
    )
    
    res = st.session_state.get('scrape_result', {})
    
    card_number_in_potential = ""
    name_default = res.get('card_name', "")
    set_default = res.get('card_set', "")
    rarity_default = res.get('card_rarity', "") 
    color_default = res.get('card_color', "") 
    img_url_default = res.get('image_url', "")
    
    if selected_label and selected_label != '':
        card_number_in_potential = card_options[selected_label]
        selected_card_info = st.session_state['unique_cards'][st.session_state['unique_cards']['card_number'] == card_number_in_potential].iloc[0]
        name_default = selected_card_info['card_name']
        set_default = selected_card_info['card_set']
        rarity_default = selected_card_info['rarity']
        color_default = selected_card_info['color']
        img_url_default = selected_card_info['image_url']
    elif res.get('card_number'):
        card_number_in_potential = res.get('card_number')
        
    card_number_in = st.text_input(
        "或手动输入/修正卡牌编号:", 
        value=card_number_in_potential, 
        key=f"card_number_in_manual_{suffix}"
    )

    existing_card_data = check_card_exists(card_number_in, st.session_state['unique_cards'])
    is_existing_card = existing_card_data is not None
    
    if card_number_in:
        if is_existing_card:
            st.info(f"✅ 卡牌编号 **{card_number_in}** 已存在。当前模式：**【更新价格历史】**")
            
            # 使用现有数据覆盖默认值
            name_default = existing_card_data.get('card_name', name_default)
            set_default = existing_card_data.get('card_set', set_default)
            rarity_default = existing_card_data.get('rarity', rarity_default)
            color_default = existing_card_data.get('color', color_default)
            img_url_default = existing_card_data.get('image_url', img_url_default)
            
            with st.form(key=f"price_update_form_{suffix}"):
                st.subheader("💰 提交新的价格记录")
                st.markdown(f"**卡牌名称:** `{name_default}`")
                st.markdown(f"**系列/版本:** `{set_default}`")
                
                st.divider()
                # 价格默认为空
                price_in = st.number_input("价格 (¥)", min_value=0.0, step=10.0, value=None, key=f"price_in_form_{suffix}")
                quantity_in = st.number_input("数量 (张)", min_value=1, step=1, key=f"quantity_in_form_{suffix}")
                date_in = st.date_input("录入日期", value=st.session_state['last_entry_date'], key=f"date_in_form_{suffix}")
                
                submitted = st.form_submit_button("提交价格更新", type="primary")

                if submitted:
                    if price_in is not None and price_in > 0 and quantity_in > 0:
                        with st.spinner("🚀 数据即时保存中..."):
                            add_card(name_default, card_number_in, set_default, price_in, quantity_in, rarity_default, color_default, date_in, img_url_default)
                        
                        st.session_state['last_entry_date'] = date_in
                        st.session_state['scrape_result'] = {}
                        st.session_state['form_key_suffix'] += 1
                        st.session_state['submission_successful'] = True
                        st.session_state['submitted_card_name'] = name_default
                        st.rerun() 
                    else:
                        st.error("价格和数量必须填写且大于 0！")

        else:
            st.warning(f"⚠️ 卡牌编号 **{card_number_in}** 未找到。当前模式：**【新增卡牌定义】**")
            with st.form(key=f"new_card_entry_form_{suffix}"):
                st.subheader("🆕 填写新卡牌信息")
                
                name_in = st.text_input("卡牌名称 (必填)", value=name_default, key=f"name_in_form_{suffix}")
                set_in = st.text_input("系列/版本", value=set_default, key=f"set_in_form_{suffix}") 
                rarity_in = st.text_input("等级 (Rarity)", value=rarity_default, key=f"rarity_in_form_{suffix}") 
                color_in = st.text_input("颜色 (例如: 紫)", value=color_default, key=f"color_in_form_{suffix}") 
                
                # 价格默认为空
                price_in = st.number_input("价格 (¥)", min_value=0.0, step=10.0, value=None, key=f"price_in_initial_form_{suffix}")
                quantity_in = st.number_input("数量 (张)", min_value=1, step=1, key=f"quantity_in_initial_form_{suffix}")
                date_in = st.date_input("录入日期", value=st.session_state['last_entry_date'], key=f"date_in_initial_form_{suffix}")

                st.divider()
                st.write("🖼️ 卡牌图片 (可修正)")
                image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default, key=f"image_url_input_form_{suffix}")
                final_image_path = image_url_input if image_url_input else None
                if final_image_path:
                    try: st.image(final_image_path, caption="预览", use_container_width=True)
                    except: st.warning("无法加载该链接的图片。")

                submitted = st.form_submit_button("提交新卡牌及初始记录", type="primary")

                if submitted:
                    if name_in and card_number_in and price_in is not None and price_in > 0 and quantity_in > 0:
                        with st.spinner("🚀 数据即时保存中..."):
                            add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
                        
                        st.session_state['last_entry_date'] = date_in
                        st.session_state['scrape_result'] = {}
                        st.session_state['form_key_suffix'] += 1
                        st.session_state['submission_successful'] = True
                        st.session_state['submitted_card_name'] = name_in
                        st.rerun() 
                    else:
                        st.error("卡牌名称、编号、价格和数量不能为空！")
    else:
        st.info("请先输入或选择卡牌编号以开始录入。")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

if st.session_state.get('autosave_successful'):
    if "❌" in st.session_state['autosave_message']:
        st.error(st.session_state['autosave_message'])
    else:
        st.success(st.session_state['autosave_message'])
    st.session_state['autosave_successful'] = False
    st.session_state['autosave_message'] = ""
    
if st.session_state.get('submission_successful'):
    card_name = st.session_state.get('submitted_card_name', '一张卡牌')
    st.success(f"✅ 已成功录入: **{card_name}**。页面已自动返回顶部。")
    # 强制滚动脚本
    components.html('<script>window.parent.scrollTo(0,0);</script>', height=0)
    st.session_state['submission_successful'] = False
    st.session_state['submitted_card_name'] = ""

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据。")
else:
    st.markdown("### 🔍 多维度筛选")
    col_s1, col_s2, col_s3, col_s4 = st.columns([3, 3, 3, 1]) 
    
    with col_s1: search_name = st.text_input("搜索 名称/编号/ID", value=st.session_state["search_name_input"], help="支持模糊搜索", key="search_name_input") 
    with col_s2: search_set = st.text_input("搜索 系列/版本", value=st.session_state["search_set_input"], key="search_set_input")
    with col_s3: date_range = st.date_input("搜索 时间范围", value=st.session_state.get("date_range_input", []), help="请选择开始和结束日期", key="date_range_input")
    with col_s4: 
        st.write(" ") 
        st.button("清空筛选", key="clear_filters_btn", use_container_width=True, on_click=clear_search_filters_action) 

    filtered_df = df.copy()
    if search_name:
        cleaned_search_name = normalize_text_for_fuzzy_search(search_name)
        search_target = (
            filtered_df['card_name'].astype(str).apply(normalize_text_for_fuzzy_search) + 
            filtered_df['card_number'].astype(str).apply(normalize_text_for_fuzzy_search) + 
            filtered_df['id'].astype(str).apply(normalize_text_for_fuzzy_search)
        )
        search_condition = search_target.str.contains(cleaned_search_name, case=False, na=False)
        filtered_df = filtered_df[search_condition]
        
    if search_set:
        filtered_df = filtered_df[filtered_df['card_set'].str.contains(search_set, case=False, na=False)]
    if len(date_range) == 2:
        filtered_df = filtered_df[(filtered_df['date_dt'].dt.date >= date_range[0]) & (filtered_df['date_dt'].dt.date <= date_range[1])]

    # --- 📝 数据编辑区域 ---
    st.markdown("### 📝 数据编辑（自动增量保存模式）")
    st.caption("✨ **自动增量保存**：修改内容后点击表格外任意处，系统自动保存。")
    st.caption("✅ **整行删除**：表格**最左侧**是**行选择复选框**。勾选后按 **`Delete`** 键删除。")
    
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore')
    display_df['date'] = display_df['date'].astype(str)
    
    display_df = display_df.sort_values(by='id', ascending=False)
    display_df = display_df.reset_index(drop=True) 
    
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    display_df_editor = display_df[['id'] + FINAL_DISPLAY_COLUMNS]

    if display_df_editor.empty:
        st.info("没有找到符合筛选条件的数据可供编辑。")
        if "data_editor" not in st.session_state:
            st.session_state["data_editor"] = {"edited_rows": {}, "deleted_rows": []}
    else:
        column_config_dict = {
            "id": st.column_config.Column("ID", disabled=True, width=50), 
            "date": st.column_config.DateColumn("录入时间", width=80, format="YYYY-MM-DD"), 
            "card_number": st.column_config.Column("编号", width=70),
            "card_name": st.column_config.Column("卡名", width=200), 
            "card_set": st.column_config.Column("系列", width=100), 
            "price": st.column_config.NumberColumn("价格 (¥)", format="¥%d", width=70),
            "quantity": st.column_config.NumberColumn("数量 (张)", format="%d", width=50),
            "rarity": st.column_config.Column("等级", width=50), 
            "color": st.column_config.Column("颜色", width=50), 
            "image_url": st.column_config.ImageColumn("卡图", width=50),
        }
        
        edited_df = st.data_editor(
            display_df_editor, 
            key="data_editor",
            hide_index=True,
            column_order=['id'] + FINAL_DISPLAY_COLUMNS,
            column_config=column_config_dict,
            num_rows="fixed",
            selection_mode="multi-row",
            use_container_width=True
        )

    editor_state = st.session_state.get("data_editor")
    if editor_state and (editor_state.get("edited_rows") or editor_state.get("deleted_rows")):
        st.info("🔄 检测到修改，正在自动增量保存...")
        with st.spinner("🚀 数据增量自动保存中..."):
            save_incremental_changes(display_df_editor, editor_state)
        st.rerun()

    st.divider()
    st.markdown("### 📊 单卡深度分析")
    
    if len(st.session_state['unique_cards']) == 0:
         st.info("无卡牌可供分析。")
    else:
        analysis_options = st.session_state['unique_cards']['display_label'].unique()
        selected_variant_label = st.selectbox("请选择要分析的具体卡牌:", analysis_options, key='analysis_select')
        
        selected_card_number = st.session_state['unique_cards'][
            st.session_state['unique_cards']['display_label'] == selected_variant_label
        ]['card_number'].iloc[0]
        
        target_df = df[df['card_number'] == selected_card_number].sort_values("date_dt")
        
        col_img, col_stat, col_chart = st.columns([1, 1, 2])
        with col_img:
            st.caption("卡牌快照 (最近一笔)")
            latest_img = target_df.iloc[-1]['image_url'] if not target_df.empty else None
            if latest_img:
                try: st.image(latest_img, use_container_width=True) 
                except: st.error("图片加载失败")
            else:
                st.empty(); st.caption("暂无图片")

        with col_stat:
            st.caption("价格统计")
            if not target_df.empty:
                curr_price = target_df.iloc[-1]['price']
                total_quantity = target_df['quantity'].sum()
                avg_price = target_df['price'].mean()
                max_price = target_df['price'].max()
                max_price_date = target_df[target_df['price'] == max_price]['date'].iloc[0] if not target_df[target_df['price'] == max_price].empty else "N/A"
                min_price = target_df['price'].min()
                min_price_date = target_df[target_df['price'] == min_price]['date'].iloc[0] if not target_df[target_df['price'] == min_price].empty else "N/A"

                c1, c2 = st.columns(2)
                c1.metric("💰 最新成交", f"¥{curr_price:,.0f}")
                c2.metric("📦 总库存", f"{total_quantity:,} 张")
                st.divider()
                c3, c4 = st.columns(2)
                c3.metric("📈 历史最高", f"¥{max_price:,.0f}", f"于 {max_price_date} 录入")
                c4.metric("📉 历史最低", f"¥{min_price:,.0f}", f"于 {min_price_date} 录入")
                st.metric("📊 平均价格", f"¥{avg_price:,.2f}")
                st.caption(f"共 {len(target_df)} 条记录")
            else:
                st.info("无数据统计。")

        with col_chart:
            st.caption("价格走势图")
            if len(target_df) > 1:
                st.line_chart(target_df, x="date_dt", y="price", color="#FF4B4B")
            else:
                st.info("需至少两条记录绘制走势")
        
        if not target_df.empty:
            st.markdown("#### 🕒 最近10次录入记录")
            recent_10_df = target_df.sort_values("date_dt", ascending=False).head(10)
            recent_display = recent_10_df[['date', 'price', 'quantity']].copy()
            recent_display.rename(columns={'date': '录入日期', 'price': '价格 (¥)', 'quantity': '数量 (张)'}, inplace=True)
            st.dataframe(recent_display, hide_index=True, use_container_width=True, column_config={"价格 (¥)": st.column_config.NumberColumn(format="¥%d"), "数量 (张)": st.column_config.NumberColumn(format="%d")})
    
    st.divider()
    st.markdown("### 📥 数据导出 (用于备份或迁移)")
    if not df.empty:
        csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(label="下载完整的卡牌数据 (CSV)", data=csv_data, file_name='card_data_full_export.csv', mime='text/csv')
