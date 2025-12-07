import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import gspread 
import gspread_dataframe as gd

# === 配置 ===
SHEET_NAME = "数据表" 
YUYU_TEI_BASE_IMAGE_URL = 'https://card.yuyu-tei.jp/opc/front/' 

# 定义新的 Google Sheets 字段顺序 (与手动修改后的表格列头一致)
# 新顺序: id, date, number, name, set, price, quantity, rarity, color, image_url
NEW_EXPECTED_COLUMNS = ['id', 'date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']

# === Gspread 数据库函数 ===

@st.cache_resource(ttl=None)
def connect_gspread():
    """使用 Streamlit Secrets 凭证连接到 Google Sheets API"""
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
        
        base_url = spreadsheet_url.split('/edit')[0] 
        sh = gc.open_by_url(base_url)
        
        return sh
    except Exception as e:
        st.error(f"无法连接 Google Sheets API。请检查 Secrets 格式和权限。错误: {e}")
        return None

@st.cache_data(ttl=3600)
def load_data():
    """从 Google Sheets 读取所有数据"""
    sh = connect_gspread()
    if not sh:
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)
    
    try:
        worksheet = sh.worksheet(SHEET_NAME) 
        df = gd.get_as_dataframe(worksheet)
        
        # 检查新的列头
        if df.empty or not all(col in df.columns for col in NEW_EXPECTED_COLUMNS):
            st.warning("Google Sheets 列头结构与代码预期不符。请检查 Google Sheets 的第一行是否已按要求修改。")
            return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

        # 确保 ID 字段是正确的整数
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        if df['id'].duplicated().any() or (df['id'] == 0).any():
             st.info("数据中发现缺失或重复 ID，已自动重新生成 ID。")
             df['id'] = range(1, len(df) + 1)
        
        # 确保列顺序与 NEW_EXPECTED_COLUMNS 一致
        df = df[NEW_EXPECTED_COLUMNS] 

        return df.sort_values(by='date', ascending=False)
    except Exception as e:
        st.error(f"无法读取工作表 '{SHEET_NAME}'。请确保工作表名称正确。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

# 新增/追加卡牌 (新增 rarity)
def add_card(name, number, card_set, price, quantity, rarity, color, date, image_url=None):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
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
            rarity,       # 新增 rarity 字段
            color,        # color 字段
            image_url if image_url else ""
        ]
        
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        st.cache_data.clear()
        st.cache_resource.clear()
        
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
        df = load_data()
        
        # 过滤掉要删除的行
        df_updated = df[df['id'] != card_id]
        
        # 确保只保留 NEW_EXPECTED_COLUMNS
        df_final = df_updated[NEW_EXPECTED_COLUMNS]
        
        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.cache_data.clear()
        st.cache_resource.clear()
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
        
        # 确保只保留 NEW_EXPECTED_COLUMNS 并进行类型清理
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        
        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("数据修改已自动保存到 Google 表格！")
    except Exception as e:
        st.error(f"保存修改失败。错误: {e}")


# 网页抓取函数 (Mercari 逻辑 - 新增 rarity 提取)
def scrape_card_data(url):
    st.info(f"正在尝试从 {url} 抓取数据...")
    if not url.startswith("http"):
        return {"error": "网址格式不正确，必须以 http 或 https 开头。"}
    
    try:
        # 使用 Headers 伪装浏览器，以避免被网站屏蔽
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status() 
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.content, 'html.parser')

        # 尝试查找 Mercari 的主要卡牌标题标签
        name_tag = soup.find(['h1', 'h2'], class_=re.compile(r'heading|title', re.I))
        full_title = name_tag.get_text(strip=True) if name_tag else ""
        
        if not full_title:
             return {"error": "未能找到卡牌名称标题。"}

        # 示例: シャーロット・プリン【プロモ】《紫》 [『プロモーションパックEXVol.3』OP12-071]
        card_name = "N/A"
        rarity = "N/A"
        color = "N/A"
        card_number = "N/A"
        card_set = "" 
        temp_title = full_title # 使用临时变量进行逐步匹配和清除

        # 1. 提取 rarity (【】中间的内容)
        rarity_match = re.search(r'【(.+?)】', temp_title)
        if rarity_match:
            rarity = rarity_match.group(1).strip()
            # 移除 【...】 部分
            temp_title = temp_title.replace(rarity_match.group(0), ' ').strip()
        
        # 2. 提取 color (《》中间的内容)
        color_match = re.search(r'《(.+?)》', temp_title)
        if color_match:
            color = color_match.group(1).strip()
            # 移除 《...》 部分
            temp_title = temp_title.replace(color_match.group(0), ' ').strip()
        
        # 3. 提取 card_number (英文-数字)
        number_match = re.search(r'([A-Z]{2,}\d{1,}\-\d{3,})', temp_title) # 稍微放宽匹配，兼容 OP12-071
        if number_match:
            card_number = number_match.group(1).strip()
            # 移除编号部分
            temp_title_without_number = temp_title[:number_match.start()] + temp_title[number_match.end():]
        else:
            temp_title_without_number = temp_title
        
        # 4. 提取 card_set 和 card_name (剩下的部分)
        
        # 提取 card_name (通常在最前面)
        name_part = re.match(r'(.+?)[\s\[『]', temp_title_without_number.strip())
        if name_part:
            card_name = name_part.group(1).strip()
            card_set = temp_title_without_number[len(name_part.group(0)):].strip()
        else:
            # 如果没有找到分隔符，假设整个开头是卡名
            card_name = temp_title_without_number.strip()
            card_set = ""
            
        # 清理 card_set 中的多余符号，如 []、『』
        card_set = re.sub(r'[\[\]『』]', '', card_set).strip()
        
        # --- 5. 提取图片链接 (尝试从 Mercari 的主要图片标签中获取) ---
        # Mercari 图片通常在 data-src 或 src 属性中，且分辨率较高
        image_tag = soup.find('img', {'alt': lambda x: x and 'メイン画像' in x}) or \
                    soup.find('img', {'alt': lambda x: x and card_name in x})
        
        image_url = None
        if image_tag:
            image_url = image_tag.get('data-src') or image_tag.get('src')
        
        if not image_url:
            st.warning("未能从 Mercari 页面中找到图片链接。请尝试从其他来源抓取，或手动粘贴。")

        return {
            "card_name": card_name,
            "card_number": card_number,
            "card_set": card_set,
            "card_rarity": rarity,   # 返回 rarity
            "card_color": color,     # 返回 color
            "image_url": image_url,
            "error": None
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"网络错误或无法访问: {e}"}
    except Exception as e:
        return {"error": f"解析网页时发生错误: {e}. 请检查HTML结构是否变化。"}

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
    
def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['scrape_url_input'] = ""
    
# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")

# --- 侧边栏：录入 ---
with st.sidebar:
    st.header("🌐 网页自动填充 (Mercari等)")
    
    scrape_url = st.text_input("输入卡牌详情页网址:", 
                               key='scrape_url_input') 
    
    col_scrape_btn, col_clear_btn = st.columns(2)
    
    with col_scrape_btn:
        if st.button("一键抓取并填充", type="secondary"):
            if not scrape_url:
                 st.warning("请输入网址后再点击抓取。")
            else:
                st.session_state['scrape_result'] = scrape_card_data(scrape_url)
                if st.session_state['scrape_result']['error']:
                    st.error(st.session_state['scrape_result']['error'])
                else:
                    st.success("数据抓取完成，已自动填充下方表单。")
                 
    with col_clear_btn:
        st.button("一键清除录入内容", type="primary", on_click=clear_all_data)

    st.divider()
    st.header("📝 手动录入/修正")
    
    # 预填充抓取结果
    name_default = st.session_state['scrape_result'].get('card_name', "")
    number_default = st.session_state['scrape_result'].get('card_number', "")
    set_default = st.session_state['scrape_result'].get('card_set', "")
    rarity_default = st.session_state['scrape_result'].get('card_rarity', "") # 重新引入 rarity
    color_default = st.session_state['scrape_result'].get('card_color', "") 
    img_url_default = st.session_state['scrape_result'].get('image_url', "")

    # 录入字段顺序: 1.编号 -> 2.名称 -> 3.版本 -> 4.等级 -> 5.颜色 -> 6.价格 -> 7.数量 -> 8.日期
    card_number_in = st.text_input("1. 卡牌编号", value=number_default)
    name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default)
    set_in = st.text_input("3. 系列/版本", value=set_default) 
    rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default) # 等级输入
    color_in = st.text_input("5. 颜色 (例如: 紫)", value=color_default) # 颜色输入
    
    price_in = st.number_input("6. 价格 (¥)", min_value=0.0, step=10.0)
    quantity_in = st.number_input("7. 数量 (张)", min_value=1, step=1)
    
    date_in = st.date_input("8. 录入日期", datetime.now())

    st.divider()
    st.write("🖼️ 卡牌图片 (可修正)")
    img_source = st.radio("选择图片来源:", ["无", "网络链接"], horizontal=True, 
                          index=1 if img_url_default else 0) 

    final_image_path = None
    
    if img_source == "网络链接":
        image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default)
        if image_url_input:
            try:
                st.image(image_url_input, caption="预览", use_container_width=True)
                final_image_path = image_url_input
            except: 
                st.error("无法加载该链接的图片，请检查网址是否正确。")
    
    if img_source == "网络链接":
        final_image_path = image_url_input
    else:
        final_image_path = None

    if st.button("提交录入", type="primary"):
        if name_in:
            # 顺序: name, number, set, price, quantity, rarity, color, date, image_url
            add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            st.session_state['scrape_result'] = {}
            st.success(f"已录入: {name_in} - ¥{price_in} x {quantity_in} 张")
            st.rerun()
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

df = load_data()

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据。")
else:
    # 预处理
    df['date_dt'] = pd.to_datetime(df['date'], errors='coerce')
    df['image_url'] = df['image_url'].fillna('')
    df['rarity'] = df['rarity'].fillna('') # 新增 rarity 预处理
    df['color'] = df['color'].fillna('') 
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int) 
    df = df.dropna(subset=['date_dt']) 
    
    # --- 🔍 多维度筛选 ---
    st.markdown("### 🔍 多维度筛选")
    col_s1, col_s2, col_s3, col_s4, col_s5, col_s6 = st.columns(6) # 增加一列给 rarity
    with col_s1: search_name = st.text_input("搜索 名称 (模糊)")
    with col_s2: search_number = st.text_input("搜索 编号 (模糊)")
    with col_s3: search_set = st.text_input("搜索 系列/版本 (模糊)")
    with col_s4: search_rarity = st.text_input("搜索 等级 (模糊)") # 筛选 rarity
    with col_s5: search_color = st.text_input("搜索 颜色 (模糊)") 
    with col_s6: date_range = st.date_input("搜索 时间范围", value=[], help="请选择开始和结束日期")

    # 筛选逻辑
    filtered_df = df.copy()
    if search_name:
        filtered_df = filtered_df[filtered_df['card_name'].str.contains(search_name, case=False, na=False)]
    if search_number:
        filtered_df = filtered_df[filtered_df['card_number'].str.contains(search_number, case=False, na=False)]
    if search_set:
        filtered_df = filtered_df[filtered_df['card_set'].str.contains(search_set, case=False, na=False)]
    if search_rarity:
        filtered_df = filtered_df[filtered_df['rarity'].str.contains(search_rarity, case=False, na=False)] # 筛选 rarity
    if search_color:
        filtered_df = filtered_df[filtered_df['color'].str.contains(search_color, case=False, na=False)] 
    if len(date_range) == 2:
        filtered_df = filtered_df[(filtered_df['date_dt'].dt.date >= date_range[0]) & (filtered_df['date_dt'].dt.date <= date_range[1])]

    # 准备用于展示和编辑的 DataFrame
    display_df = filtered_df.drop(columns=['date_dt'], errors='ignore')

    st.markdown("### 📝 数据编辑和删除（双击单元格修改）")
    
    # 定义最终呈现的列顺序
    # 录入时间、编号、卡名、系列、价格、数量、等级、颜色、卡图
    FINAL_DISPLAY_COLUMNS = ['date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']
    
    # 确保 display_df 包含 'id' 和 '删除' 才能正确使用 data_editor 和 ButtonColumn
    display_df = display_df[['id'] + FINAL_DISPLAY_COLUMNS]
    display_df['删除'] = '🗑️ 删除'
    
    # 配置列显示名称和格式
    column_config_dict = {
        "id": st.column_config.Column("ID", disabled=True), 
        "date": st.column_config.DateColumn("录入时间"), 
        "card_number": "编号",
        "card_name": "卡名",
        "card_set": "系列",
        "price": st.column_config.NumberColumn("价格 (¥)", format="¥%d"),
        "quantity": st.column_config.NumberColumn("数量 (张)", format="%d"),
        "rarity": "等级", # 中文显示名称
        "color": "颜色",
        "image_url": st.column_config.ImageColumn("卡图", width="small"),
        "删除": st.column_config.ButtonColumn("删除记录", help="点击删除该行数据", on_click=delete_card, args=['id'])
    }
    
    # 使用 st.data_editor 实现表格编辑功能
    edited_df = st.data_editor(
        display_df,
        key="data_editor",
        use_container_width=True, 
        hide_index=True,
        column_order=['id', '删除'] + FINAL_DISPLAY_COLUMNS, # 调整显示顺序
        column_config=column_config_dict,
    )

    # 检查是否有编辑变动
    if st.session_state["data_editor"]["edited_rows"] or st.session_state["data_editor"]["deleted_rows"]:
        st.caption("检测到数据修改，请点击 **保存修改** 按钮。")
        
        # 提取未删除的数据
        final_df_to_save = edited_df.drop(columns=['删除'], errors='ignore')
        
        if st.button("💾 确认并保存所有修改", type="primary"):
            update_data_and_save(final_df_to_save)
            st.rerun()

    
    st.divider()

    # --- 📊 深度分析面板 ---
    st.markdown("### 📊 单卡深度分析")
    
    analysis_df = filtered_df.copy() 

    if analysis_df.empty:
        st.warning("无筛选结果。")
    else:
        # 按卡牌名称、编号、等级和颜色来区分唯一变体
        analysis_df['unique_label'] = analysis_df['card_name'] + " [" + analysis_df['card_number'] + " " + analysis_df['rarity'] + " " + analysis_df['color'] + "]"
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
                
                st.metric("最近成交价", f"¥{curr_price:,.0f}")
                st.metric("📈 历史最高 / 📉 最低", f"¥{target_df['price'].max():,.0f} / ¥{target_df['price'].min():,.0f}")
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
