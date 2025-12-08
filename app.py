import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import gspread 
import gspread_dataframe as gd
import numpy as np 
import time # 用于在写入 Google Sheets 后等待，确保操作完成

# === 配置 ===
SHEET_NAME = "数据表" 
# 定义 Google Sheets 字段顺序
NEW_EXPECTED_COLUMNS = ['id', 'date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
if 'form_key_suffix' not in st.session_state: 
    st.session_state['form_key_suffix'] = 0
# 标记：指示数据是否已更新，用于强制缓存刷新
if 'data_is_fresh' not in st.session_state:
    st.session_state['data_is_fresh'] = False
# 存储待删除的行ID列表
if 'rows_to_delete' not in st.session_state:
    st.session_state['rows_to_delete'] = set()
# 存储当前 DataFrame，用于即时更新
if 'current_df' not in st.session_state:
    st.session_state['current_df'] = pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
    
def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['form_key_suffix'] += 1 
    st.session_state['data_is_fresh'] = False
    st.session_state['rows_to_delete'] = set() # 清除待删除列表

# === 辅助函数：模糊搜索规范化 ===
def normalize_text_for_fuzzy_search(text):
    """
    移除空格和连字符，并转换为大写，用于忽略格式的模糊搜索匹配。
    """
    if pd.isna(text):
        return ""
    cleaned = str(text).replace('-', '').replace(' ', '')
    return cleaned.upper()

# === Gspread 数据库函数 ===

@st.cache_resource(ttl=None)
def connect_gspread():
    """使用 Streamlit Secrets 凭证连接到 Google Sheets API"""
    try:
        # ... (连接逻辑保持不变)
        creds = {
            "type": st.secrets["gsheets"]["type"],
            "project_id": st.secrets["gsheets"]["project_id"],
            "private_key_id": st.secrets["gsheets"]["private_key_id"],
            "private_key": st.secrets["gsheets"]["private_key"],
            "client_email": st.secrets["gsheets"]["client_email"],
            "client_id": st.secrets["gsheets"]["client_id"],
            "auth_uri": st.secrets["gsheets"]["auth_uri"],
            "token_uri": st.secrets["gsheets"]["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["gsheets"]["auth_provider_x509_cert_url"],
            "client_x509_cert_url": st.secrets["gsheets"]["client_x509_cert_url"],
            "universe_domain": st.secrets["gsheets"]["universe_domain"]
        }
        
        gc = gspread.service_account_from_dict(creds)
        spreadsheet_url = st.secrets["gsheets"]["spreadsheet_url"]
        
        base_url = spreadsheet_url.split('/edit')[0] 
        sh = gc.open_by_url(base_url)
        
        return sh
    except Exception as e:
        st.error(f"无法连接 Google Sheets API。请检查 Secrets 格式、权限及 URL。错误: {e}")
        return None

# load_data 函数接受 'force_refresh' 作为参数，强制缓存更新
@st.cache_data(ttl=3600)
def load_data(force_refresh):
    """从 Google Sheets 读取所有数据"""
    if force_refresh is False:
        # 如果 force_refresh 为 False (默认状态)，则使用缓存
        pass 
    
    sh = connect_gspread()
    if not sh:
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
    
    try:
        worksheet = sh.worksheet(SHEET_NAME) 
        df = gd.get_as_dataframe(worksheet)
        
        if df.empty or not all(col in df.columns for col in NEW_EXPECTED_COLUMNS):
            st.warning("Google Sheets 列头结构与代码预期不符。")
            return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

        # 数据清洗和 ID 确保
        df = df.replace({np.nan: None}) 
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        if df['id'].duplicated().any() or (df['id'] == 0).any():
             df['id'] = range(1, len(df) + 1)
        
        df = df[NEW_EXPECTED_COLUMNS] 
        df = df.sort_values(by='date', ascending=False)
        
        # 缓存数据并将其存储到 session_state
        st.session_state['current_df'] = df.copy() 
        return df
    except Exception as e:
        st.error(f"无法读取工作表 '{SHEET_NAME}'。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

# 新增/追加卡牌
def add_card(name, number, card_set, price, quantity, rarity, color, date, image_url=None):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        # 从 session_state 中获取当前数据来确定新 ID
        df_current = st.session_state['current_df']
        
        try:
            max_id = pd.to_numeric(df_current['id'], errors='coerce').max()
            new_id = int(max_id + 1) if pd.notna(max_id) else 1
        except:
            new_id = 1
        
        new_row = [
            new_id, 
            date.strftime('%Y-%m-%d'),
            number, 
            name, 
            card_set, 
            price, 
            quantity, 
            rarity,       
            color,        
            image_url if image_url else ""
        ]
        
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        time.sleep(5.0) # 保持 5 秒等待，但在后台执行
        
        st.cache_data.clear()
        st.cache_resource.clear()
        st.session_state['data_is_fresh'] = True # 强制下次 load_data 时重新读取
        
    except Exception as e:
        st.error(f"追加数据到 Sheets 失败。错误: {e}")

# 处理数据编辑器的内容和待删除行，并保存到 Google Sheets
def update_data_and_save(edited_df, rows_to_delete):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        
        # 1. 应用编辑 (edited_df)
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        df_to_save = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        
        # 2. 从 df_to_save 中移除待删除行 (双重检查)
        if rows_to_delete:
            df_final = df_to_save[~df_to_save['id'].isin(rows_to_delete)]
        else:
            df_final = df_to_save

        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        time.sleep(5.0) # 保持 5 秒等待，保证写入成功
        
        # 清理状态
        st.cache_data.clear()
        st.cache_resource.clear()
        st.session_state['data_is_fresh'] = True # 强制下次 load_data 时重新读取
        st.session_state['rows_to_delete'] = set() # 清空待删除列表
        st.success("数据修改已自动保存到 Google 表格！")
        return True
    except Exception as e:
        st.error(f"保存修改失败。错误: {e}")
        return False

# ... (scrape_card_data 函数保持不变)
def scrape_card_data(url):
    # ... (保持不变)
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

        card_name = ""; rarity = ""; color = ""; card_number = ""; card_set = "" 
        temp_title = full_title # 初始化临时标题

        # 1. 提取 rarity (例如：【R】)
        rarity_match = re.search(r'【(.+?)】', temp_title)
        if rarity_match:
            rarity = rarity_match.group(1).strip()
            temp_title = temp_title.replace(rarity_match.group(0), '').strip()
        
        # 2. 提取 color (例如：《红》)
        color_match = re.search(r'《(.+?)》', temp_title)
        if color_match:
            color = color_match.group(1).strip()
            temp_title = temp_title.replace(color_match.group(0), '').strip()
        
        # 3. 提取 card_number (例如：P-028 或 EB03-061)
        number_match = re.search(r'([A-Z0-9]{1,}\-\d{2,})', temp_title) 
        
        if number_match:
            card_number = number_match.group(1).strip()
            temp_title = temp_title.replace(number_match.group(0), '').strip()
        
        # 4. 提取 card_set 和 card_name
        cleaned_title = temp_title.strip()
        
        # 尝试提取各种括号内的系列/版本信息 (支持全角/半角)
        # 匹配 [内容] 或 (内容) 或 『内容』
        card_set_match = re.search(r'[\(\[（『](.+?)[\)\]）』]', cleaned_title)
        
        if card_set_match:
            # 提取括号内的内容作为系列名
            card_set = card_set_match.group(1).strip()
            # 从标题中移除括号及内容，剩下的就是卡名
            card_name = cleaned_title.replace(card_set_match.group(0), '').strip()
        else:
            # 如果没有明显的括号包裹的系列信息，整个剩余的字符串就是卡名
            card_name = cleaned_title
            card_set = ""
            
        # 确保卡名不为空
        if not card_name:
             card_name = cleaned_title 

        # --- 5. 提取图片链接 ---
        image_url = None
        
        # 优先级 1: 尝试通过 og:image meta 标签获取 (适用于 Mercari 等网站)
        og_image_tag = soup.find('meta', property='og:image')
        if og_image_tag:
            image_url = og_image_tag.get('content')
            
        # 优先级 2: 如果未通过 og:image 获取，则尝试旧的 img 标签搜索
        if not image_url:
            # 使用更宽泛的搜索
            image_tag = soup.find('img', {'alt': lambda x: x and ('メイン画像' in x or 'カード' in x)}) or \
                        soup.find('img', {'src': lambda x: x and ('card_image' in x or 'images' in x)})
            
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
        # 记录详细的解析错误
        return {"error": f"解析错误 (可能在标题或图片提取): {e}"}

# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")

# 获取动态 key suffix (用于在提交/清除后重置所有 input 控件)
suffix = str(st.session_state['form_key_suffix'])

# --- 侧边栏：录入 ---
with st.sidebar:
    # ... (录入部分保持不变，但提交按钮 now uses spinner)
    st.header("🌐 网页自动填充")
    scrape_url = st.text_input("输入卡牌详情页网址:", key=f'scrape_url_input_{suffix}') 
    
    col_scrape_btn, col_clear_btn = st.columns(2)
    
    with col_scrape_btn:
        if st.button("一键抓取并填充", type="secondary"):
            if not scrape_url:
                 st.warning("请输入网址。")
            else:
                st.session_state['scrape_result'] = scrape_card_data(scrape_url)
                if st.session_state['scrape_result']['error']:
                    st.error(st.session_state['scrape_result']['error'])
                else:
                    st.success("数据抓取完成。")
                st.session_state['form_key_suffix'] += 1
                st.rerun()
                 
    with col_clear_btn:
        if st.button("一键清除录入内容", type="primary"):
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

    card_number_in = st.text_input("1. 卡牌编号", value=number_default, key=f"card_number_in_{suffix}")
    name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default, key=f"name_in_{suffix}")
    set_in = st.text_input("3. 系列/版本", value=set_default, key=f"set_in_{suffix}") 
    rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default, key=f"rarity_in_{suffix}") 
    color_in = st.text_input("5. 颜色 (例如: 紫)", value=color_default, key=f"color_in_{suffix}") 
    
    price_in = st.number_input("6. 价格 (¥)", min_value=0.0, step=10.0, key=f"price_in_{suffix}")
    quantity_in = st.number_input("7. 数量 (张)", min_value=1, step=1, key=f"quantity_in_{suffix}")
    
    date_in = st.date_input("8. 录入日期", datetime.now(), key=f"date_in_{suffix}")

    st.divider()
    st.write("🖼️ 卡牌图片 (可修正)")

    image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default, key=f"image_url_input_{suffix}")
    final_image_path = image_url_input if image_url_input else None
    
    if final_image_path:
        try:
            st.image(final_image_path, caption="预览", use_container_width=True)
        except: 
            st.warning("无法加载该链接的图片。")

    if st.button("提交录入", type="primary"):
        if name_in:
            with st.spinner("🚀 数据保存中... Google Sheets への書き込み完了のため、5.0秒お待ちください..."):
                add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            st.session_state['scrape_result'] = {}
            st.session_state['form_key_suffix'] += 1 
            
            st.success(f"已录入: {name_in}")
            st.rerun() 
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

# 修正: load_data にフラグを渡すことで、キャッシュを厳密に制御
df = load_data(st.session_state['data_is_fresh']) 

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
    
    # 将原始 DataFrame 存储到 session state
    st.session_state['current_df'] = df.copy()

    # --- 🔍 多维度筛选 ---
    st.markdown("### 🔍 多维度筛选")
    col_s1, col_s2, col_s3 = st.columns(3) 
    with col_s1: search_name = st.text_input("搜索 名称/编号/ID", help="支持模糊搜索，例如输入 'P 113' 也能匹配 'P-113' 或包含 'P113' 的卡牌名称") 
    with col_s2: search_set = st.text_input("搜索 系列/版本")
    with col_s3: date_range = st.date_input("搜索 时间范围", value=[], help="请选择开始和结束日期")

    # 筛选逻辑
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

    # --- 🗑️ 删除逻辑处理 ---
    # 在 filtered_df 中移除待删除的行 (即时删除)
    filtered_df = filtered_df[~filtered_df['id'].isin(st.session_state['rows_to_delete'])]
    
    # 准备用于展示和编辑的 DataFrame
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore').copy()
    display_df['date'] = pd.to_datetime(display_df['date'], errors='coerce').dt.date 

    st.markdown("### 📝 数据编辑与删除 (即时删除)") 
    
    # --- 按钮点击回调处理 ---
    # 检查是否有删除按钮被点击
    if 'data_editor' in st.session_state and 'edited_rows' in st.session_state['data_editor']:
        
        # 查找被点击的删除按钮的行索引
        delete_clicks = st.session_state['data_editor']['edited_rows']
        
        for index, edits in delete_clicks.items():
            if 'delete_button' in edits and edits['delete_button']:
                # 获取被点击行的 ID
                deleted_id = display_df.iloc[index]['id']
                
                # 1. 标记为待删除
                st.session_state['rows_to_delete'].add(deleted_id)
                
                # 2. 清除 data_editor 的状态，防止无限循环
                del st.session_state['data_editor']['edited_rows'][index]['delete_button']
                
                # 3. 强制重新运行脚本，实现即时删除效果
                st.rerun()

    # --- 表格配置 ---
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    
    display_df = display_df[['id'] + FINAL_DISPLAY_COLUMNS]
    
    column_config_dict = {
        "id": st.column_config.Column("ID", disabled=True), 
        "date": st.column_config.DateColumn("录入时间"), 
        "card_number": "编号",
        "card_name": "卡名",
        "card_set": "系列",
        "price": st.column_config.NumberColumn("价格 (¥)", format="¥%d"),
        "quantity": st.column_config.NumberColumn("数量 (张)", format="%d"),
        "rarity": "等级", 
        "color": "颜色",
        "image_url": st.column_config.ImageColumn("卡图", width="small"),
        # 新增删除按钮列
        "delete_button": st.column_config.ButtonColumn("删除", help="点击即时删除该行", width="small", disabled=False), 
    }
    
    edited_df = st.data_editor(
        display_df.assign(delete_button='删除'), # 必须添加一个基础列，才能配置 ButtonColumn
        key="data_editor",
        use_container_width=True, 
        hide_index=True,
        # 禁用 data_editor 自带的行删除功能
        num_rows="fixed", # 阻止用户手动添加或删除行
        column_order=['id'] + FINAL_DISPLAY_COLUMNS + ['delete_button'],
        column_config=column_config_dict,
    )

    # 检查是否有编辑变动或待删除行
    has_edits = st.session_state["data_editor"]["edited_rows"]
    has_deletes = len(st.session_state['rows_to_delete']) > 0
    
    if has_edits or has_deletes:
        
        # 从 edited_df 中移除 'delete_button' 列
        final_df_to_save = edited_df.drop(columns=['delete_button'], errors='ignore')

        st.caption(f"检测到 {len(st.session_state['rows_to_delete'])} 条待删除记录，及 {len(has_edits)} 条修改。请点击 **保存修改** 按钮。")
        
        if st.button("💾 确认并保存所有修改", type="primary"):
            with st.spinner("🚀 数据保存中... 写入 Google Sheets 并清除待删除记录，请稍候 5.0 秒"):
                # 保存逻辑：将 edited_df (应用编辑) 和 rows_to_delete (应用删除) 写入 Sheets
                success = update_data_and_save(final_df_to_save, st.session_state['rows_to_delete'])
            
            if success:
                st.rerun() # 强制刷新
            
    st.divider()
    
    # --- 📊 单卡深度分析面板 ---
    # ... (分析部分保持不变)
    st.markdown("### 📊 单卡深度分析")
    
    analysis_df = filtered_df.copy() 

    if analysis_df.empty:
        st.warning("无筛选结果。")
    else:
        # ... (分析图表和指标逻辑保持不变)
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
                
                max_price = target_df['price'].max()
                max_price_date = target_df[target_df['price'] == max_price]['date'].iloc[0]
                
                min_price = target_df['price'].min()
                min_price_date = target_df[target_df['price'] == min_price]['date'].iloc[0]

                st.metric("最近成交价", f"¥{curr_price:,.0f}")
                
                st.markdown(f"**📈 历史最高**：¥{max_price:,.0f} (于 **{max_price_date}** 录入)")
                st.markdown(f"**📉 历史最低**：¥{min_price:,.0f} (于 **{min_price_date}** 录入)")
                
                st.metric("💰 平均价格", f"¥{target_df['price'].mean():,.2f}")
                st.metric("📦 总库存数量", f"{total_quantity:,} 张")
                st.write(f"共 {len(target_df)} 条记录")
            else:
                st.info("无数据统计。")


        with col_chart:
            st.caption("价格走势图")
            if len(target_df) > 1:
                st.line_chart(target_df, x="date_dt", y="price", color="#FF4B4B")
            else:
                st.info("需至少两条记录绘制走势")
