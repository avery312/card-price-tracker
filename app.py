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
    
# 新增 Session state for autosave messages (用于显示自动保存结果)
if 'autosave_successful' not in st.session_state:
    st.session_state['autosave_successful'] = False
if 'autosave_message' not in st.session_state:
    st.session_state['autosave_message'] = ""
    
# 新增 Session state for filter persistence (用于保持筛选状态)
# 暂时禁用筛选状态的 Session state 赋值，以简化
# if 'date_range_input' not in st.session_state:
#     st.session_state['date_range_input'] = [] 
# if 'search_name_input' not in st.session_state:
#     st.session_state['search_name_input'] = ""
# if 'search_set_input' not in st.session_state:
#     st.session_state['search_set_input'] = ""


def clear_all_data():
    """清除所有录入相关 Session State。"""
    st.session_state['scrape_result'] = {} 
    st.session_state['form_key_suffix'] += 1 
    st.session_state['last_entry_date'] = datetime.now().date() 

# 禁用 clear_search_filters_action 因为之前没有定义相关的 session state 变量
# def clear_search_filters_action():
#     """清除所有筛选相关的 Session State 变量。用于 on_click 回调。"""
#     st.session_state["search_name_input"] = ""
#     st.session_state["search_set_input"] = ""
#     st.session_state["date_range_input"] = [] 


# === 辅助函数：模糊搜索规范化 ===
def normalize_text_for_fuzzy_search(text):
    if pd.isna(text):
        return ""
    cleaned = str(text).replace('-', '').replace(' ', '')
    return cleaned.upper()

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
        
        df = df[NEW_EXPECTED_COLUMNS] 

        return df
    except Exception as e:
        st.error(f"无法从 Supabase 读取数据。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

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

# 【核心功能：实现全量保存，以简化 data_editor 的复杂状态处理】
def update_data_and_save(edited_df: pd.DataFrame):
    """
    删除 Supabase 中的所有数据，然后重新插入编辑后的 DataFrame 中的所有数据。
    """
    supabase = connect_supabase()
    if not supabase: return
    
    try:
        # 1. 数据类型清理和格式化
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        # 确保编辑后的 price 和 quantity 是正确的数值类型
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0.0).astype(float)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        data_to_save = df_final.to_dict('records')

        # 2. 核心操作：删除所有旧数据，然后重新插入所有新数据
        # 注意：这里假设 Supabase 中的 'id' 字段是自增的，但我们必须依靠 DataFrame 中的 id
        # 由于我们使用 st.data_editor，如果允许添加行，可能会有新的 id，但我们这里只处理编辑和删除
        
        # 为简单起见，我们使用一个快速的全表删除再插入（如果数据量不大）
        # 实际生产中应使用 UPSERT 或增量更新，但 Streamlit 的 data_editor 状态难以精确映射。
        # 我们可以尝试使用事务来确保数据一致性，但 Streamlit 不支持。
        
        # 为了避免全量删除丢失数据，我们只删除被修改过的记录 (这在全量更新逻辑中不适用)
        # 最简单稳定的方式：使用编辑后的 DataFrame 覆盖整个表（前提是 edited_df 包含所有数据）

        # 针对您上一个版本代码中的全量保存逻辑，我们保留它
        supabase.table(SUPABASE_TABLE_NAME).delete().neq('id', 0).execute() 

        if data_to_save:
            supabase.table(SUPABASE_TABLE_NAME).insert(data_to_save).execute()
        
        st.session_state['autosave_successful'] = True
        st.session_state['autosave_message'] = "✅ 数据修改已保存到 Supabase！"

    except Exception as e:
        st.session_state['autosave_successful'] = True
        st.session_state['autosave_message'] = f"❌ 保存修改失败。错误: {e}"


# 网页抓取函数 (保持不变)
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

        name_tag = soup.find(['h1', 'h2'], class_=re.compile(r'heading|title', re.I))
        full_title = name_tag.get_text(strip=True) if name_tag else ""
        
        if not full_title:
             return {"error": "未能找到卡牌名称标题。"}

        card_name = "N/A"; rarity = "N/A"; color = "N/A"; card_number = "N/A"; card_set = "" 
        temp_title = full_title 

        # 1. 提取 rarity
        rarity_match = re.search(r'【(.+?)】', temp_title)
        if rarity_match:
            rarity = rarity_match.group(1).strip()
            temp_title = temp_title.replace(rarity_match.group(0), ' ').strip()
        
        # 2. 提取 color
        color_match = re.search(r'《(.+?)》', temp_title)
        if color_match:
            color = color_match.group(1).strip()
            temp_title = temp_title.replace(color_match.group(0), ' ').strip()
        
        # 3. 提取 card_number
        number_match = re.search(r'([A-Z0-9]{1,}\-\d{2,})', temp_title) 
        
        if number_match:
            card_number = number_match.group(1).strip()
            temp_title_without_number = temp_title[:number_match.start()] + temp_title[number_match.end():]
        else:
            temp_title_without_number = temp_title
        
        # 4. 提取 card_set 和 card_name
        name_part = re.match(r'(.+?)[\s\[『]', temp_title_without_number.strip())
        if name_part:
            card_name = name_part.group(1).strip()
            card_set = temp_title_without_number[len(name_part.group(0)):].strip()
        else:
            card_name = temp_title_without_number.strip()
            card_set = ""
            
        card_set = re.sub(r'[\[\]『』]', '', card_set).strip()
        
        # --- 5. 提取图片链接 ---
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

# --- 侧边栏：录入 ---
with st.sidebar:
    if st.session_state.get('submission_successful'):
        card_name = st.session_state.get('submitted_card_name', '一张卡牌')
        st.success(f"✅ **{card_name}** 录入成功！", icon="🎉") 
        
    st.header("🌐 网页自动填充")
    scrape_url = st.text_input("输入卡牌详情页网址:", key=f'scrape_url_input_{suffix}') 
    
    col_scrape_btn, col_clear_btn = st.columns(2)
    
    with col_scrape_btn:
        if st.button("一键抓取并填充", type="secondary", key=f"scrape_btn_{suffix}"):
            if not scrape_url: st.warning("请输入网址。")
            else:
                st.session_state['scrape_result'] = scrape_card_data(scrape_url)
                if st.session_state['scrape_result']['error']: st.error(st.session_state['scrape_result']['error'])
                else: st.success("数据抓取完成。")
                st.session_state['form_key_suffix'] += 1
                st.rerun() 
                 
    with col_clear_btn:
        if st.button("一键清除录入内容", type="primary", key=f"clear_btn_{suffix}"):
            clear_all_data()
            st.rerun() 

    st.divider()
    st.header("📝 手动录入/修正")
    
    res = st.session_state['scrape_result']
    name_default = res.get('card_name', "")
    number_default = res.get('card_number', "")
    set_default = res.get('card_set', "")
    rarity_default = res.get('card_rarity', "") 
    color_default = res.get('card_color', "") 
    img_url_default = res.get('image_url', "")

    
    with st.form(key=f"manual_entry_form_{suffix}"):
        card_number_in = st.text_input("1. 卡牌编号", value=number_default, key=f"card_number_in_form_{suffix}")
        name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default, key=f"name_in_form_{suffix}")
        set_in = st.text_input("3. 系列/版本", value=set_default, key=f"set_in_form_{suffix}") 
        rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default, key=f"rarity_in_form_{suffix}") 
        color_in = st.text_input("5. 颜色 (例如: 紫)", value=color_default, key=f"color_in_form_{suffix}") 
        
        price_in = st.number_input("6. 价格 (¥)", min_value=0.0, step=10.0, key=f"price_in_form_{suffix}")
        quantity_in = st.number_input("7. 数量 (张)", min_value=1, step=1, key=f"quantity_in_form_{suffix}")
        
        date_in = st.date_input(
            "8. 录入日期", 
            value=st.session_state['last_entry_date'],
            key=f"date_in_form_{suffix}"
        )

        st.divider()
        st.write("🖼️ 卡牌图片 (可修正)")

        image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default, key=f"image_url_input_form_{suffix}")
        final_image_path = image_url_input if image_url_input else None
        
        if final_image_path:
            try:
                st.image(final_image_path, caption="预览", use_container_width=True)
            except: 
                st.warning("无法加载该链接的图片。")

        submitted = st.form_submit_button("提交录入", type="primary")

    if submitted:
        if name_in:
            with st.spinner("🚀 数据即时保存中..."):
                add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            st.session_state['last_entry_date'] = date_in

            st.session_state['scrape_result'] = {}
            st.session_state['form_key_suffix'] += 1
            
            st.session_state['submission_successful'] = True
            st.session_state['submitted_card_name'] = name_in
            
            st.rerun() 
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

# 检查并显示保存结果
if st.session_state.get('autosave_successful'):
    if "❌" in st.session_state['autosave_message']:
        st.error(st.session_state['autosave_message'])
    else:
        st.success(st.session_state['autosave_message'])
        
    st.session_state['autosave_successful'] = False
    st.session_state['autosave_message'] = ""
    
# 检查并显示录入结果
if st.session_state.get('submission_successful'):
    card_name = st.session_state.get('submitted_card_name', '一张卡牌')
    st.success(f"✅ 已成功录入: **{card_name}**。页面已自动返回顶部。")
    st.session_state['submission_successful'] = False
    st.session_state['submitted_card_name'] = ""

df = load_data() 

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据。")
else:
    # 预处理
    df['date_dt'] = pd.to_datetime(df['date'], errors='coerce')
    df['image_url'] = df['image_url'].fillna('')
    df['rarity'] = df['rarity'].fillna('') 
    df['color'] = df['color'].fillna('') 
    df['card_set'] = df['card_set'].fillna('') 
    df['card_number'] = df['card_number'].fillna('') 
    
    # 【修正区域】：确保 price 和 quantity 的类型一致性
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0.0).astype(float)
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int) 
    
    df = df.dropna(subset=['date_dt']) 
    
    # --- 🔍 多维度筛选 ---
    st.markdown("### 🔍 多维度筛选")
    
    col_s1, col_s2, col_s3 = st.columns(3) 
    
    with col_s1: 
        search_name = st.text_input("搜索 名称/编号/ID", help="支持模糊搜索") 
    with col_s2: 
        search_set = st.text_input("搜索 系列/版本")
    with col_s3: 
        date_range = st.date_input(
            "搜索 时间范围", 
            value=[], 
            help="请选择开始和结束日期"
        )

    # --- 筛选逻辑 (用于编辑和分析) ---
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
        # 确保 date_range 包含两个日期
        filtered_df = filtered_df[(filtered_df['date_dt'].dt.date >= date_range[0]) & (filtered_df['date_dt'].dt.date <= date_range[1])]

    
    # --- 📝 数据编辑区域 ---
    
    st.markdown("### 📝 数据编辑（编辑后需手动保存）")
    st.caption("✨ **修改/删除**：双击单元格修改内容，或选中行后按 `Delete` 键删除。完成后请点击下方的 **保存修改** 按钮。")
    
    # 准备用于展示和编辑的 DataFrame (使用筛选结果)
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore').copy()

    # 1. 核心修正：确保日期是兼容 st.data_editor 的 datetime.date 类型
    # 将原始的日期字符串（或对象）转换为 datetime 对象
    date_series = pd.to_datetime(display_df['date'], errors='coerce')
    
    # 填充 NaT 值：使用今天日期来替换任何无效或缺失的日期
    date_series = date_series.fillna(datetime.now())
    
    # 转换为 Python 原生的 date 对象，以最大化与 st.column_config.DateColumn 的兼容性
    display_df['date'] = date_series.dt.date

    # 核心排序逻辑：根据 ID 从大到小（最新的在最上面）进行初始排序
    display_df = display_df.sort_values(by='id', ascending=False)
    
    # 强制重置索引 (这一步是必要的，但 Streamlit 的 data_editor 会自行处理索引映射)
    display_df = display_df.reset_index(drop=True) 

    
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    
    # 确保 ID 列在最前面
    display_df = display_df[['id'] + FINAL_DISPLAY_COLUMNS]

    if display_df.empty:
        st.info("没有找到符合筛选条件的数据可供编辑。")
        # 确保 session state 中存在 data_editor 键，防止后续逻辑报错
        if "data_editor" not in st.session_state:
            st.session_state["data_editor"] = {"edited_rows": {}, "deleted_rows": []}
        edited_df = display_df.copy() # 如果是空的，edited_df 也是空的
    else:
        column_config_dict = {
            "id": st.column_config.Column("ID", disabled=True, width=50), 
            "date": st.column_config.DateColumn("录入时间", width=80), 
            "card_number": st.column_config.Column("编号", width=70),
            "card_name": st.column_config.Column("卡名", width=200), 
            "card_set": st.column_config.Column("系列", width=100), 
            # 使用 NumberColumn 确保数据类型正确
            "price": st.column_config.NumberColumn("价格 (¥)", format="¥%d", width=70),
            "quantity": st.column_config.NumberColumn("数量 (张)", format="%d", width=50),
            "rarity": st.column_config.Column("等级", width=50), 
            "color": st.column_config.Column("颜色", width=50), 
            "image_url": st.column_config.ImageColumn("卡图", width=50),
        }
        
        # Line 422: st.data_editor call
        edited_df = st.data_editor(
            display_df, 
            key="data_editor", # 核心：将编辑状态存入 session state
            hide_index=True,
            column_order=['id'] + FINAL_DISPLAY_COLUMNS,
            column_config=column_config_dict,
            num_rows="dynamic", # 允许用户添加新行
            selection_mode="multi-row",
        )

    # 【核心保存逻辑】
    editor_state = st.session_state.get("data_editor", {})
    
    # 检查是否有编辑变动、删除操作或新增行
    if editor_state.get("edited_rows") or editor_state.get("deleted_rows") or (len(edited_df) > len(display_df)):
        st.warning("⚠️ 数据修改、新增或删除操作已检测到。请点击 **保存修改** 按钮！")
        
        # 由于我们使用全量保存，我们直接将 data_editor 返回的 DataFrame 传递给保存函数
        final_df_to_save = edited_df.copy() 
        
        if st.button("💾 确认并保存所有修改", type="primary"):
            with st.spinner("🚀 数据即时保存中..."):
                # 调用全量保存函数
                update_data_and_save(final_df_to_save)
            
            # 必须调用 rerun 来刷新数据，清除 data_editor 的状态，并显示保存成功的消息
            st.rerun()

    
    st.divider()
    
    # --- 📊 单卡深度分析面板 (使用筛选结果) ---
    st.markdown("### 📊 单卡深度分析")
    
    analysis_df = filtered_df.copy() 

    if analysis_df.empty:
        st.warning("无筛选结果。")
    else:
        analysis_df['unique_label'] = analysis_df.apply(
            lambda x: f"{x['card_name']} [{x['card_number']}] ({x['card_set']}) - {x['rarity']}/{x['color']}", 
            axis=1
        )
        
        unique_variants = analysis_df['unique_label'].unique()
        selected_variant = st.selectbox("请选择要分析的具体卡牌:", unique_variants)
        
        target_df = analysis_df[analysis_df['unique_label'] == selected_variant].sort_values("date_dt")
        
        col_img, col_stat, col_chart = st.columns([1, 1, 2])
        
        with col_img:
            st.caption("卡牌快照 (最近一笔)")
            latest_img = target_df.iloc[-1]['image_url'] if not target_df.empty else None
            if latest_img:
                try:
                    st.image(latest_img, use_container_width=True) 
                except:
                    st.error("图片加载失败")
            else:
                st.empty()
                st.caption("暂无图片")

        with col_stat:
            st.caption("价格统计")
            if not target_df.empty:
                curr_price = target_df.iloc[-1]['price']
                total_quantity = target_df['quantity'].sum()
                avg_price = target_df['price'].mean()
                
                max_price = target_df['price'].max()
                # 确保在取日期时，df不是空的，且日期是有效的
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
        
        # 最近10次录入记录表格
        if not target_df.empty:
            st.markdown("#### 🕒 最近10次录入记录")
            
            recent_10_df = target_df.sort_values("date_dt", ascending=False).head(10)
            
            recent_display = recent_10_df[['date', 'price', 'quantity']].copy()
            
            recent_display.rename(columns={
                'date': '录入日期',
                'price': '价格 (¥)',
                'quantity': '数量 (张)'
            }, inplace=True)
            
            st.dataframe(
                recent_display, 
                hide_index=True, 
                use_container_width=True,
                column_config={
                    "价格 (¥)": st.column_config.NumberColumn(format="¥%d"),
                    "数量 (张)": st.column_config.NumberColumn(format="%d")
                }
            )
    
    # --- 📥 数据导出 (用于备份或迁移) ---
    st.divider()
    st.markdown("### 📥 数据导出 (用于备份或迁移)")
    if not df.empty:
        csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            label="下载完整的卡牌数据 (CSV)",
            data=csv_data,
            file_name='card_data_full_export.csv',
            mime='text/csv',
            help='点击下载 Supabase 中的所有数据，用于备份。'
        )
