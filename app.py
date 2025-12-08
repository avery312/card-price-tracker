import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import gspread 
import gspread_dataframe as gd
import numpy as np 
import time # 保持导入，尽管在最终代码中未使用

# === 配置 ===
SHEET_NAME = "数据表" 
# 定义 Google Sheets 字段顺序
NEW_EXPECTED_COLUMNS = ['id', 'date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
    
def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['scrape_url_input'] = ""

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
    """使用 Streamlit Secrets 凭证连接到 Google Sheets API (缓存连接对象)"""
    try:
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
        
        # 兼容性处理：去除 URL 中的 gid 参数
        base_url = spreadsheet_url.split('/edit')[0] 
        sh = gc.open_by_url(base_url)
        
        return sh
    except Exception as e:
        st.error(f"无法连接 Google Sheets API。请检查 Secrets 格式、权限及 URL。错误: {e}")
        return None

# 🔑 关键：移除 @st.cache_data 装饰器，强制每次脚本运行时都读取最新数据
def load_data():
    """从 Google Sheets 读取所有数据 (无缓存，即时读取)"""
    sh = connect_gspread()
    if not sh:
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
    
    try:
        worksheet = sh.worksheet(SHEET_NAME) 
        df = gd.get_as_dataframe(worksheet)
        
        if df.empty or not all(col in df.columns for col in NEW_EXPECTED_COLUMNS):
            # 尝试修复空表时的列头问题
            if df.empty:
                return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
            st.warning("Google Sheets 列头结构与代码预期不符。")
            return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

        # 数据清洗和 ID 确保
        df = df.replace({np.nan: None}) # 将 NaN 替换为 None
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        
        # 重新生成 ID 以确保不重复，如果发现 ID 重复或为 0
        if df['id'].duplicated().any() or (df['id'] == 0).any():
             # 仅在需要时才重新生成 ID，并确保是连续的
             df.loc[:, 'id'] = range(1, len(df) + 1)
        
        # 确保列顺序
        df = df[NEW_EXPECTED_COLUMNS] 

        # 根据 ID 降序排序，确保最新记录在顶部
        return df.sort_values(by='id', ascending=False)
    except Exception as e:
        st.error(f"无法读取工作表 '{SHEET_NAME}'。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

# 新增/追加卡牌
def add_card(name, number, card_set, price, quantity, rarity, color, date, image_url=None):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        
        # 为了获取正确的 max_id，我们必须读取最新数据。
        df = load_data() 
        
        try:
            max_id = pd.to_numeric(df['id'], errors='coerce').max()
            new_id = int(max_id + 1) if pd.notna(max_id) else 1
        except:
            new_id = 1
        
        # 准备要追加的行数据 (必须与 NEW_EXPECTED_COLUMNS 顺序一致)
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
        
    except Exception as e:
        st.error(f"追加数据到 Sheets 失败。错误: {e}")

# 删除卡牌函数
def delete_card(card_id):
    sh = connect_gspread()
    if not sh: 
        st.error("无法连接 Google Sheets。")
        return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        # 强制读取最新数据
        df = load_data() 
        
        # 过滤掉要删除的行
        df_updated = df[df['id'] != card_id]
        
        # 确保只保留 NEW_EXPECTED_COLUMNS
        df_final = df_updated[NEW_EXPECTED_COLUMNS].replace({None: ''}) 
        
        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.success(f"ID {card_id} 记录已删除！正在刷新页面...")
        st.rerun() 
        
    except Exception as e:
        st.error(f"删除数据失败。错误: {e}")
        
# 处理数据编辑器的内容并保存到 Google Sheets
def update_data_and_save(edited_df):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        
        # 数据类型清理和格式化
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        # 确保列顺序并处理缺失值
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        
        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.success("数据修改已自动保存到 Google 表格！")
    except Exception as e:
        st.error(f"保存修改失败。错误: {e}")


# 网页抓取函数 
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


# --- 侧边栏：录入 ---
with st.sidebar:
    st.header("🌐 网页自动填充")
    
    # 将 key 移动到 session state 之外以避免冲突
    scrape_url = st.text_input("输入卡牌详情页网址:", key='scrape_url_input') 
    
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
                 
    with col_clear_btn:
        # 使用 on_click 触发函数
        st.button("一键清除录入内容", type="primary", on_click=clear_all_data)

    st.divider()
    st.header("📝 手动录入/修正")
    
    # 预填充抓取结果
    res = st.session_state['scrape_result']
    name_default = res.get('card_name', "")
    number_default = res.get('card_number', "")
    set_default = res.get('card_set', "")
    rarity_default = res.get('card_rarity', "") 
    color_default = res.get('card_color', "") 
    img_url_default = res.get('image_url', "")

    # 🔑 使用 st.form 确保输入字段状态和提交操作的原子性
    with st.form(key="manual_entry_form"):
        # 录入字段
        card_number_in = st.text_input("1. 卡牌编号", value=number_default, key="card_number_in")
        name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default, key="name_in")
        set_in = st.text_input("3. 系列/版本", value=set_default, key="set_in") 
        rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default, key="rarity_in") 
        color_in = st.text_input("5. 颜色 (例如: 紫)", value=color_default, key="color_in") 
        
        price_in = st.number_input("6. 价格 (¥)", min_value=0.0, step=10.0, key="price_in")
        quantity_in = st.number_input("7. 数量 (张)", min_value=1, step=1, key="quantity_in")
        
        date_in = st.date_input("8. 录入日期", datetime.now(), key="date_in")

        st.divider()
        st.write("🖼️ 卡牌图片 (可修正)")

        image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default, key="image_url_input_form")
        final_image_path = image_url_input if image_url_input else None
        
        if final_image_path:
            try:
                st.image(final_image_path, caption="预览", use_container_width=True)
            except: 
                st.warning("无法加载该链接的图片。")

        # 使用 st.form_submit_button
        submitted = st.form_submit_button("提交录入", type="primary")

    if submitted:
        if name_in:
            with st.spinner("🚀 数据即时保存中..."):
                add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            st.session_state['scrape_result'] = {}
            st.success(f"已录入: {name_in}")
            # 强制重新执行脚本
            st.rerun()
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

# 🔑 每次脚本运行时都会执行，并从 Google Sheets 读取最新数据
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

    # 准备用于展示和编辑的 DataFrame
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore')

    # 强制将 'date' 列从字符串转换为 datetime 对象
    display_df['date'] = pd.to_datetime(display_df['date'], errors='coerce') 

    st.markdown("### 📝 数据编辑（双击单元格修改）")
    
    # 定义最终呈现的列顺序
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    
    # 确保 display_df 包含 'id'
    display_df = display_df[['id'] + FINAL_DISPLAY_COLUMNS]
    
    # 配置列显示名称和格式 
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
    }
    
    # 使用 st.data_editor 实现表格编辑功能
    edited_df = st.data_editor(
        display_df,
        key="data_editor",
        use_container_width=True, 
        hide_index=True,
        column_order=['id'] + FINAL_DISPLAY_COLUMNS,
        column_config=column_config_dict,
    )

    # 检查是否有编辑变动
    if st.session_state["data_editor"]["edited_rows"] or st.session_state["data_editor"]["deleted_rows"]:
        st.caption("检测到数据修改，请点击 **保存修改** 按钮。")
        
        final_df_to_save = edited_df
        
        if st.button("💾 确认并保存所有修改", type="primary"):
            update_data_and_save(final_df_to_save)
            st.rerun()

    
    st.divider()
    
    # --- ❌ 手动删除记录 ---
    st.markdown("### ❌ 手动删除记录")
    
    if not filtered_df.empty:
        delete_options = filtered_df.sort_values(by='date', ascending=False).apply(
            lambda x: f"ID {x['id']} | {x['date']} | {x['card_name']} [{x['card_number']}] ({x['card_set']}) - {x['rarity']}/{x['color']} @ ¥{x['price']:,.0f}", 
            axis=1
        )
        
        col_del_select, col_del_btn = st.columns([3, 1])
        
        with col_del_select:
            if not delete_options.empty:
                selected_delete_option = st.selectbox("选择要删除的记录:", delete_options)
            else:
                selected_delete_option = None
        
        if selected_delete_option:
            delete_id_match = re.search(r'ID (\d+)\s*\|', selected_delete_option)
            card_id_to_delete = int(delete_id_match.group(1)) if delete_id_match else None
            
            with col_del_btn:
                 st.markdown("<br>", unsafe_allow_html=True)
                 if st.button("🔴 确认删除所选记录", type="secondary"):
                     if card_id_to_delete:
                         delete_card(card_id_to_delete)
                     else:
                         st.error("无法识别要删除的记录 ID。")
    else:
        st.info("没有可删除的记录。")
        
    st.divider()

    # --- 📊 单卡深度分析面板 ---
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
                st.line_chart(target_df, x="date", y="price", color="#FF4B4B")
            else:
                st.info("需至少两条记录绘制走势")

    # =========================================================================
    # 🔑 移动到最底部：数据导出功能
    # =========================================================================
    st.divider()
    st.markdown("### 📥 数据导出 (用于备份或迁移)")
    
    # 确保使用完整的 DataFrame df 进行导出，而不是筛选后的 filtered_df
    if not df.empty:
        csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            label="下载完整的卡牌数据 (CSV)",
            data=csv_data,
            file_name='card_data_full_export.csv',
            mime='text/csv',
            help="点击下载 Google 表格中的所有数据，用于备份。"
        )
