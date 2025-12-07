import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
# 替换为 gspread 及其辅助库
import gspread 
# 修正导入方式：改为导入整个库并使用别名
import gspread_dataframe as gd

# === 配置 ===
# 注意: 如果您的 Google Sheets 标签页名称不是“数据表”，请在此处修改!
SHEET_NAME = "数据表" 
YUYU_TEI_BASE_IMAGE_URL = 'https://card.yuyu-tei.jp/opc/front/' 

# === Gspread 数据库函数 (使用标准 gspread 库) ===

@st.cache_resource(ttl=None)
def connect_gspread():
    """使用 Streamlit Secrets 凭证连接到 Google Sheets API"""
    try:
        # 使用 st.secrets 直接加载 TOML 配置中的凭证
        # 确保 Secrets TOML 文件中的键名一致
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
        
        # 移除 URL 中的 gid 参数，确保 open_by_url 正确打开整个文档
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
    # 如果连接失败，返回空数据框
    if not sh:
        return pd.DataFrame(columns=['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url'])
    
    try:
        # 获取指定名称的工作表
        worksheet = sh.worksheet(SHEET_NAME) 
        
        # 使用 gspread-dataframe 读取为 DataFrame (修正: 使用别名 gd)
        df = **gd.get_dataframe**(worksheet)
        
        # 确保列头匹配
        expected_columns = ['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url']
        if df.empty or not all(col in df.columns for col in expected_columns):
            # 如果表格为空或结构不正确，返回一个带有预期列的空数据框
            return pd.DataFrame(columns=expected_columns)

        return df.sort_values(by='date', ascending=False)
    except Exception as e:
        st.error(f"无法读取工作表 '{SHEET_NAME}'。请确保工作表名称正确。错误: {e}")
        return pd.DataFrame(columns=['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url'])


# 更新后的 add_card 函数：直接向 Sheets 追加行
def add_card(name, number, card_set, rarity, price, quantity, date, image_url=None):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        # 获取最新数据以计算 ID
        df = load_data() 
        
        # 生成新的唯一 ID
        try:
            max_id = pd.to_numeric(df['id'], errors='coerce').max()
            new_id = int(max_id + 1) if pd.notna(max_id) else 1
        except:
            new_id = 1
        
        # 准备要追加的行数据 (必须与表格列顺序一致)
        new_row = [
            new_id, 
            name, 
            number, 
            card_set, 
            rarity, 
            price, 
            quantity, 
            date.strftime('%Y-%m-%d'), # 格式化日期
            image_url if image_url else ""
        ]
        
        # 使用 gspread 的 append_row 方法追加
        worksheet.append_row(new_row, value_input_option='USER_ENTERED')
        
        # 清除缓存
        st.cache_data.clear()
        st.cache_resource.clear()
        
    except Exception as e:
        st.error(f"追加数据到 Sheets 失败。错误: {e}")

# 删除卡牌函数：通过重写整个数据框实现排除 (最安全的方法)
def delete_card(card_id):
    sh = connect_gspread()
    if not sh: return
    
    try:
        worksheet = sh.worksheet(SHEET_NAME)
        df = load_data()
        
        # 过滤掉要删除的行
        df_updated = df[pd.to_numeric(df['id'], errors='coerce') != card_id]
        
        # 确保只保留需要的列
        columns_to_keep = ['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url']
        df_final = df_updated[columns_to_keep]
        
        # 覆盖工作表 (修正: 使用别名 gd)
        **gd.set_with_dataframe**(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.cache_data.clear()
        st.cache_resource.clear()
        
    except Exception as e:
        st.error(f"删除数据失败。错误: {e}")
        
# 网页抓取函数 (保持不变)
def scrape_card_data(url):
    st.info(f"正在尝试从 {url} 抓取数据...")
    if not url.startswith("http"):
        return {"error": "网址格式不正确，必须以 http 或 https 开头。"}
    
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

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
    
# --- 清除函数 (使用 on_click 模式) ---
def clear_all_data():
    """在点击时执行的回调函数，用于清除所有 session state 数据，包括 URL 输入框的内容"""
    st.session_state['scrape_result'] = {} 
    st.session_state['scrape_url_input'] = ""
    
# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")

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
