import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import gspread 
import gspread_dataframe as gd
import numpy as np # 新增 numpy 导入，用于处理缺失值

# === 配置 ===
SHEET_NAME = "数据表" 
# 定义 Google Sheets 字段顺序
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
        
        # 兼容性处理：去除 URL 中的 gid 参数
        base_url = spreadsheet_url.split('/edit')[0] 
        sh = gc.open_by_url(base_url)
        
        return sh
    except Exception as e:
        st.error(f"无法连接 Google Sheets API。请检查 Secrets 格式、权限及 URL。错误: {e}")
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
        
        if df.empty or not all(col in df.columns for col in NEW_EXPECTED_COLUMNS):
            st.warning("Google Sheets 列头结构与代码预期不符。")
            return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

        # 数据清洗和 ID 确保
        df = df.replace({np.nan: None}) # 将 NaN 替换为 None，便于后续处理
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        if df['id'].duplicated().any() or (df['id'] == 0).any():
             df['id'] = range(1, len(df) + 1)
        
        # 确保列顺序
        df = df[NEW_EXPECTED_COLUMNS] 

        return df.sort_values(by='date', ascending=False)
    except Exception as e:
        st.error(f"无法读取工作表 '{SHEET_NAME}'。错误: {e}")
        return pd.DataFrame(columns=NEW_EXPECTED_COLUMNS)

# 新增/追加卡牌
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
            rarity,       
            color,        
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
        df_final = df_updated[NEW_EXPECTED_COLUMNS].replace({None: ''}) # 转换 None 为空字符串以写入 Sheets
        
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
        
        # 数据类型清理和格式化
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        # 确保列顺序并处理缺失值
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        
        # 覆盖工作表
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.cache_data.clear()
        st.cache_resource.clear()
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
        number_match = re.search(r'([A-Z]{2,}\d{1,}\-\d{3,})', temp_title) 
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
        image_tag = soup.find('img', {'alt': lambda x: x and 'メイン画像' in x}) or \
                    soup.find('img', {'alt': lambda x: x and card_name in x})
        
        image_url = None
        if image_tag:
            image_url = image_tag.get('data
