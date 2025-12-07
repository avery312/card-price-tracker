import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import uuid
from streamlit_gsheets import GSheetsConnection

# === 配置及常量 ===
SHEET_NAME = "数据表"  # 你的 Google Sheets 表格底部标签名称，请确保与你的表格名称一致
YUYU_TEI_BASE_IMAGE_URL = 'https://card.yuyu-tei.jp/opc/front/' 
# 移除 DB_NAME 和 IMAGE_FOLDER 常量

# === 数据库函数 (现为 Google Sheets 函数) ===

def get_gsheets_connection():
    """建立与 Google Sheets 的连接"""
    # 这里的 'gsheets' 对应 .streamlit/secrets.toml 中的 [gsheets] 配置
    return st.connection("gsheets", type=GSheetsConnection)

# init_db 函数现在仅用于加载数据和确保连接正常
def init_db():
    """初始化数据库，确保连接正常并返回数据"""
    try:
        conn = get_gsheets_connection()
        # 尝试读取数据，确保表存在（在 Sheets 中即为确保工作表存在）
        df = conn.read(worksheet=SHEET_NAME)
        return df
    except Exception as e:
        st.error(f"无法连接到 Google Sheets 或读取工作表 '{SHEET_NAME}'。请检查 secrets.toml 配置和工作表名称是否正确。错误: {e}")
        return pd.DataFrame() # 返回空 DataFrame 避免应用崩溃

# 移除 init_db() 中的 SQLite 表结构创建和列检查逻辑

# 替换 add_card 函数
def add_card(name, number, card_set, rarity, price, quantity, date, image_url=None):
    conn = get_gsheets_connection()
    
    # 1. 创建新的记录行
    new_data = pd.DataFrame([{
        "id": str(uuid.uuid4()), # 使用 UUID 作为唯一ID
        "card_name": name,
        "card_number": number,
        "card_set": card_set,
        "rarity": rarity,
        "price": price,
        "quantity": quantity,
        "date": date.strftime('%Y-%m-%d'), # 确保日期格式统一
        "image_url": image_url if image_url else ""
    }])
    
    # 2. 将数据追加到 Google Sheets
    conn.append(data=new_data, worksheet=SHEET_NAME)

# 替换 delete_card 函数
def delete_card(card_id):
    conn = get_gsheets_connection()
    df = load_data(conn) # 重新加载当前所有数据
    
    # 过滤掉要删除的行
    df_updated = df[df['id'] != str(card_id)]
    
    # 将整个更新后的 DataFrame 写回 Google Sheets
    # 注意：Google Sheets 写入需要覆盖模式
    conn.write(data=df_updated, worksheet=SHEET_NAME)

# 替换 load_data 函数
def load_data(conn=None):
    if conn is None:
        conn = get_gsheets_connection()
        
    try:
        # 读取整个工作表到 DataFrame
        df = conn.read(worksheet=SHEET_NAME, ttl=5) # ttl=5 秒，控制数据刷新频率
        # 确保 id 列是字符串类型，以便比较
        df['id'] = df['id'].astype(str)
        return df
    except Exception as e:
        st.error(f"加载数据失败: {e}")
        return pd.DataFrame()

# 移除 save_uploaded_image 函数

# 网页抓取函数 (保持不变，因为不涉及本地文件)
@st.cache_data(ttl=3600) 
def scrape_card_data(url):
    st.info(f"正在尝试从 {url} 抓取数据...")
    if not url.startswith("http"):
        return {"error": "网址格式不正确，必须以 http 或 https 开头。"}
    
    # (网页抓取逻辑不变...)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() 
        response.encoding = 'EUC-JP'
        soup = BeautifulSoup(response.content, 'html.parser')

        # --- 1. 提取 主标题行 (包含 等级, 名称, 版本) ---
        name_tag = soup.select_one('h1')
        full_title = name_tag.get_text(strip=True) if name_tag else ""
        
        card_name = "N/A"
        card_rarity = "N/A"
        card_set = "N/A"
        
        if full_title:
            # 1. 提取 等级 (Rarity)
            rarity_match = re.match(r'^([A-Z0-9\-]+)', full_title)
            if rarity_match:
                card_rarity = rarity_match.group(1).strip()
                remainder = full_title[len(rarity_match.group(0)):].strip()
            else:
                remainder = full_title
                card_rarity = "N/A"
            
            # 2. 提取 名称
            name_match = re.match(r'([^(\s]+)', remainder)
            if name_match:
                card_name = name_match.group(1).strip()
            else:
                card_name = remainder.strip() 

            # 3. 提取 版本 (Set)
            set_matches = re.findall(r'\(([^)]+)\)', full_title)
            if set_matches:
                card_set = " / ".join(set_matches).strip()
            else:
                card_set = "N/A"
        
        # --- 4. 提取 卡牌编号 (OP07-064) ---
        card_number = "N/A"
        number_pattern = r'[A-Z0-9]{2,}\-\d{2,}'
        
        page_text = soup.get_text()
        number_matches = re.findall(number_pattern, page_text)
        
        if number_matches:
            card_number = number_matches[0] 

        # --- 5. 提取 图片链接 ---
        match = re.search(r'yuyu-tei\.jp/sell/opc/card/([^/]+)/(\d+)', url)
        image_url = None
        if match:
            category_path = match.group(1) 
            card_id = match.group(2)       
            image_url = YUYU_TEI_BASE_IMAGE_URL + category_path + '/' + card_id + '.jpg'

        return {
            "card_name": card_name,
            "card_number": card_number,
            "card_set": card_set,
            "card_rarity": card_rarity,
            "image_url": image_url,
            "error": None
        }

    except requests.exceptions.RequestException as e:
        return {"error": f"网络错误或无法访问: {e}"}
    except Exception as e:
        return {"error": f"解析网页时发生错误: {e}. 请检查HTML结构是否变化。"}


# --- Streamlit Session State & UI ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
    
def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['scrape_url_input'] = ""
    
# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")
df = init_db() # 使用新的 init_db 加载数据

# --- 侧边栏：录入 ---
with st.sidebar:
    st.header("🌐 网页自动填充 (yuyu-tei)")
    
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
    rarity_default = st.session_state['scrape_result'].get('card_rarity', "")
    img_url_default = st.session_state['scrape_result'].get('image_url', "")

    # 录入字段顺序: 1.编号 -> 2.名称 -> 3.版本 -> 4.等级 -> 5.价格 -> 6.数量 -> 7.日期
    card_number_in = st.text_input("1. 卡牌编号", value=number_default)
    name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default)
    set_in = st.text_input("3. 系列/版本", value=set_default) 
    rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default) 
    
    # --- 字段调整：移除品相，增加数量 ---
    price_in = st.number_input("5. 价格 (¥)", min_value=0.0, step=10.0)
    quantity_in = st.number_input("6. 数量 (张)", min_value=1, step=1)
    # ------------------------------------
    
    date_in = st.date_input("7. 录入日期", datetime.now())

    st.divider()
    st.write("🖼️ 卡牌图片 (请使用网络链接)")
    
    # 移除 radio 选项，只保留 URL 输入
    image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default)
    
    final_image_path = None
    if image_url_input:
        try:
            st.image(image_url_input, caption="预览", use_container_width=True)
            final_image_path = image_url_input
        except: 
            st.error("无法加载该链接的图片，请检查网址是否正确。")


    if st.button("提交录入", type="primary"):
        if name_in:
            # final_image_path 已经是 URL
            
            # 调用更新后的 add_card 函数 (price, quantity)
            add_card(name_in, card_number_in, set_in, rarity_in, price_in, quantity_in, date_in, final_image_path)
            st.session_state['scrape_result'] = {}
            st.success(f"已录入: {name_in} - ¥{price_in} x {quantity_in} 张")
            
            # 清除缓存并重新加载数据，以便在主页面即时显示
            st.cache_data.clear() 
            st.rerun()
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 (保持不变，因为 load_data 返回的仍是 DataFrame) ---
st.title("📈 卡牌历史与价格分析 Pro")

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据，或检查 Google Sheets 连接。")
else:
    # 预处理
    # 确保 'id' 列是字符串，且 'date' 列是 datetime 对象
    df['id'] = df['id'].astype(str) 
    df['date_dt'] = pd.to_datetime(df['date'])
    df['image_url'] = df['image_url'].fillna('')
    df['rarity'] = df['rarity'].fillna('')
    df['quantity'] = df['quantity'].fillna(1).astype(int) 
    
    # ... (其余展示和分析逻辑保持不变)
    
    # (此处省略其余展示和分析逻辑，请在您的 app.py 中保留)

    # ... (接下来的代码逻辑与原代码保持一致)

    # --- 🗑️ 数据管理 ---
    with st.expander("🗑️ 数据管理 (删除记录)"):
        if not df.empty:
            # 使用整个 DataFrame，而不是 filtered_df，以确保能删除所有记录
            df_display = df.sort_values(by='date_dt', ascending=False)
            
            # 更新 del_label 以显示数量
            df_display['del_label'] = df_display.apply(lambda x: f"ID:{x['id'][:8]}... | {x['date']} | {x['card_name']} ({x['card_number']}) | ¥{x['price']} x {x['quantity']}", axis=1)
            
            del_select = st.selectbox("选择要删除的记录:", df_display['del_label'])
            
            if st.button("确认删除选中记录"):
                # 从 del_select 中提取完整的 UUID
                selected_uuid_prefix = del_select.split("|")[0].replace("ID:", "").replace("...", "").strip()
                # 找到匹配 UUID 前缀的完整 ID
                full_id_to_delete = df_display[df_display['id'].str.startswith(selected_uuid_prefix)]['id'].iloc[0]
                
                delete_card(full_id_to_delete)
                st.success("已删除！")
                st.cache_data.clear() 
                st.rerun()

# 剩余代码保持原样...