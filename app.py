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

# 【核心功能：实现增量自动保存，已修复日期非空约束问题】
def save_incremental_changes(displayed_df: pd.DataFrame, editor_state: dict):
    """
    根据 data_editor 的状态，对 Supabase 进行精确的 UPSERT 和 DELETE 操作。
    - displayed_df 是 data_editor 当前显示的 (已筛选且**已重置索引**) 的 DataFrame。
    - editor_state 包含被编辑和被删除的行索引 (基于 0-based 连续索引)。
    """
    supabase = connect_supabase()
    if not supabase: return
    
    deleted_count = 0
    updated_count = 0
    
    try:
        # 1. 处理删除操作 (DELETE)
        deleted_indices = editor_state.get("deleted_rows", [])
        if deleted_indices:
            # 根据 0-based 索引从显示的 DataFrame 中获取要删除的记录的 ID
            # 这一步是实现整行删除的关键：通过 Streamlit 返回的索引找到对应的 ID
            ids_to_delete = displayed_df.iloc[deleted_indices]['id'].tolist()
            
            if ids_to_delete:
                deleted_count = len(ids_to_delete)
                # 使用 Supabase 的 `in` 过滤器进行批量删除
                supabase.table(SUPABASE_TABLE_NAME).delete().in_('id', ids_to_delete).execute()

        # 2. 处理修改操作 (UPSERT/UPDATE)
        edited_rows = editor_state.get("edited_rows", {})
        if edited_rows:
            data_to_upsert = []
            
            for filtered_index, changes in edited_rows.items():
                # 检查这个索引是否同时被删除，如果是，则跳过（删除优先）
                if filtered_index in deleted_indices:
                    continue
                    
                # 获取原始 ID，它是更新记录的唯一标识
                row_id = displayed_df.iloc[filtered_index]['id']
                
                # 创建一个只包含 ID 和所有修改列的数据字典
                update_data = {'id': int(row_id)}
                
                # 获取原始日期对象
                original_date_obj = displayed_df.iloc[filtered_index]['date']
                
                # --- 【日期非空修正核心逻辑】 ---
                # 目标：确保 update_data 在发送给 Supabase 时始终包含 'date' 键，且值为非空字符串。
                initial_date_str = datetime.now().strftime('%Y-%m-%d') # 终极回退值
                
                # 如果 original_date_obj 是有效的日期字符串或对象，尝试提取其 YYYY-MM-DD 格式
                if original_date_obj:
                    if isinstance(original_date_obj, date):
                        initial_date_str = original_date_obj.strftime('%Y-%m-%d')
                    elif isinstance(original_date_obj, str):
                         # 假设原始字符串是有效的 YYYY-MM-DD
                        try:
                           datetime.strptime(original_date_obj, '%Y-%m-%d')
                           initial_date_str = original_date_obj
                        except:
                           # 如果不是有效格式，则保留回退值
                           pass
                
                # 无论如何，先设置一个有效的日期值 (原始值或今天的值)
                update_data['date'] = initial_date_str 
                # --- 修正结束 ---
                
                # 遍历所有修改的列及其值
                for col, value in changes.items():
                    
                    # 数据类型转换和清理
                    if col == 'date':
                        # 如果 date 被修改，则尝试使用编辑后的值进行转换和覆盖
                        final_date_str_edit = None
                        
                        if value:
                            # 1. 如果是 Streamlit DateColumn 返回的 date/datetime 对象
                            if isinstance(value, (datetime, pd.Timestamp, date)): 
                                try:
                                    final_date_str_edit = value.strftime('%Y-%m-%d')
                                except:
                                    pass 
                            # 2. 如果是字符串 (例如用户在单元格中手动输入)
                            elif isinstance(value, str):
                                # 检查字符串是否是有效的日期格式
                                try:
                                    datetime.strptime(value, '%Y-%m-%d')
                                    final_date_str_edit = value
                                except:
                                    pass
                        
                        # 只有编辑后的值有效时才覆盖 update_data['date']
                        if final_date_str_edit is not None:
                            update_data[col] = final_date_str_edit 
                        # 如果编辑值无效，将保留修正步骤中设置的 initial_date_str

                    elif col in ['price']:
                        update_data[col] = float(value) if pd.notna(value) else 0.0
                    elif col in ['quantity']:
                        update_data[col] = int(value) if pd.notna(value) else 0
                    else:
                        update_data[col] = str(value) if pd.notna(value) else ""
                        
                # 将包含 ID 和修改值的字典添加到待更新列表
                data_to_upsert.append(update_data)
            
            if data_to_upsert:
                updated_count = len(data_to_upsert)
                # Supabase UPSERT (根据主键 'id' 自动更新或插入)
                supabase.table(SUPABASE_TABLE_NAME).upsert(data_to_upsert).execute()

        
        if deleted_count > 0 or updated_count > 0:
            msg = f"✅ 已自动保存：更新 {updated_count} 条，删除 {deleted_count} 条。"
            st.session_state['autosave_successful'] = True
            st.session_state['autosave_message'] = msg
        
    except Exception as e:
        # 捕获任何可能的错误，并设置错误消息，让主程序显示
        st.session_state['autosave_successful'] = True
        st.session_state['autosave_message'] = f"❌ 自动保存失败。错误: {e}"


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

# 检查并显示自动保存结果
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
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int) 
    df = df.dropna(subset=['date_dt']) 
    
    # --- 🔍 多维度筛选 ---
    st.markdown("### 🔍 多维度筛选")
    
    col_s1, col_s2, col_s3, col_s4 = st.columns([3, 3, 3, 1]) 
    
    with col_s1: 
        # 使用 Session State 变量保持输入框的值
        search_name = st.text_input("搜索 名称/编号/ID", value=st.session_state["search_name_input"], help="支持模糊搜索", key="search_name_input") 
    with col_s2: 
        search_set = st.text_input("搜索 系列/版本", value=st.session_state["search_set_input"], key="search_set_input")
    with col_s3: 
        date_range = st.date_input(
            "搜索 时间范围", 
            value=st.session_state.get("date_range_input", []), 
            help="请选择开始和结束日期", 
            key="date_range_input"
        )
    
    with col_s4: 
        st.write(" ") 
        # 使用回调函数清空筛选状态
        st.button(
            "清空筛选", 
            key="clear_filters_btn", 
            use_container_width=True,
            on_click=clear_search_filters_action
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
    
    st.markdown("### 📝 数据编辑（自动增量保存模式）")
    st.caption("✨ **自动增量保存**：在单元格中完成修改后，点击表格外的任何位置，系统将**自动保存**您修改的内容。")
    st.caption("🚨 **安全提示**：此编辑器仅显示筛选结果。所有修改和删除将仅应用于屏幕上可见的记录。")
    st.caption("✅ **整行删除**：表格**最左侧**是**行选择复选框**。勾选一行或多行，然后按键盘上的 **`Delete`** 键即可执行删除操作。")
    
    # 准备用于展示和编辑的 DataFrame (使用筛选结果)
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore')

    # 【关键修正区域】：确保日期格式兼容 st.data_editor
    # 1. 确保 'date' 字段的空值是 None (而不是 NaT 或 NaN)
    #    - 这让 st.data_editor 更好地处理空/无效的日期输入
    #    - 将有效日期转换为 'YYYY-MM-DD' 字符串，以增强与 st.data_editor 的兼容性
    date_series = pd.to_datetime(display_df['date'], errors='coerce')
    display_df['date'] = date_series.apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) else None)
    
    display_df = display_df.sort_values(by='id', ascending=False)
    
    # 2. 强制重置索引：确保索引连续
    display_df = display_df.reset_index(drop=True) 
    
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    
    # 确保 ID 列在最前面
    display_df = display_df[['id'] + FINAL_DISPLAY_COLUMNS]

    if display_df.empty:
        st.info("没有找到符合筛选条件的数据可供编辑。")
        # 确保 session state 中存在 data_editor 键，防止后续逻辑报错
        if "data_editor" not in st.session_state:
            st.session_state["data_editor"] = {"edited_rows": {}, "deleted_rows": []}
    else:
        column_config_dict = {
            "id": st.column_config.Column("ID", disabled=True, width=50), 
            "date": st.column_config.DateColumn("录入时间", width=80), 
            "card_number": st.column_config.Column("编号", width=70),
            "card_name": st.column_config.Column("卡名", width=200), 
            "card_set": st.column_config.Column("系列", width=100), 
            "price": st.column_config.NumberColumn("价格 (¥)", format="¥%d", width=70),
            "quantity": st.column_config.NumberColumn("数量 (张)", format="%d", width=50),
            "rarity": st.column_config.Column("等级", width=50), 
            "color": st.column_config.Column("颜色", width=50), 
            "image_url": st.column_config.ImageColumn("卡图", width=50),
        }
        
        st.data_editor(
            display_df, 
            key="data_editor", # 核心：将编辑状态存入 session state
            hide_index=True,
            column_order=['id'] + FINAL_DISPLAY_COLUMNS,
            column_config=column_config_dict,
            num_rows="fixed", # 仅允许修改现有行和删除行 (允许删除现有行)
            # 确保了多行选择模式，用于显示删除复选框
            selection_mode="multi-row",
            # 移除 use_container_width=True 以确保复选框可见
        )

    # 【核心自动保存/删除逻辑】
    editor_state = st.session_state.get("data_editor")
    
    # 检查是否有编辑变动或删除操作
    # 当用户在单元格完成编辑或删除行后，st.data_editor 会自动更新 session state 并触发 rerun
    if editor_state and (editor_state.get("edited_rows") or editor_state.get("deleted_rows")):
        
        # 立即进行保存，取代手动确认按钮
        st.info("🔄 检测到修改或删除，正在自动增量保存...")
        
        with st.spinner("🚀 数据增量自动保存中..."):
            # 调用增量保存函数
            save_incremental_changes(display_df, editor_state)
        
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
            latest_img = target_df.iloc[-1]['image_url']
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
