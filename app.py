import streamlit as st
import pandas as pd
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re 
import gspread 
import gspread_dataframe as gd
import numpy as np 

# === 配置 ===
SHEET_NAME = "数据表" 
# 定义 Google Sheets 字段顺序
NEW_EXPECTED_COLUMNS = ['id', 'date', 'card_number', 'card_name', 'card_set', 'price', 'quantity', 'rarity', 'color', 'image_url']

# === 辅助函数：模糊搜索规范化 ===
def normalize_text_for_fuzzy_search(text):
    """
    移除空格和连字符，并转换为大写，用于忽略格式的模糊搜索匹配。
    例如，将 'P-113' 或 'P 113' 规范化为 'P113'。
    """
    if pd.isna(text):
        return ""
    # 移除连字符 '-' 和空格 ' '
    cleaned = str(text).replace('-', '').replace(' ', '')
    return cleaned.upper()

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

        # デフォルトのソートを ID 降順に変更
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
        
        # データ型クリーニングとフォーマット
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        # 列順序の確認と欠損値の処理
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        
        # ワークシートを上書き保存
        gd.set_with_dataframe(worksheet, df_final, row=1, col=1, include_index=False, include_column_header=True)
        
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("数据修改已自动保存到 Google 表格！") # 日本語での応答のため、メッセージはそのままにしておきます
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
        # 使用更灵活的正则表达式识别卡号 (例如 P-028 或 EB03-061)
        # 匹配格式： [1+字母/数字] - [2+数字]
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
        
        # 优先级 1: 尝试通过 og:image meta 标签获取 (适用于 Mercadop 等网站)
        og_image_tag = soup.find('meta', property='og:image')
        if og_image_tag:
            image_url = og_image_tag.get('content')
            
        # 优先级 2: 如果未通过 og:image 获取，则尝试旧的 img 标签搜索
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

# --- Streamlit Session State ---
if 'scrape_result' not in st.session_state:
    st.session_state['scrape_result'] = {}
if 'form_key_suffix' not in st.session_state: # 【新增】用于强制重置表单
    st.session_state['form_key_suffix'] = 0
    
def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['scrape_url_input'] = ""
    st.session_state['form_key_suffix'] += 1 # 【新增】递增 suffix 以重置所有输入框
    
# === 界面布局 ===
st.set_page_config(page_title="卡牌行情分析Pro", page_icon="📈", layout="wide")

# --- 侧边栏：录入 ---
with st.sidebar:
    st.header("🌐 网页自动填充")
    
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

    # 获取动态 key suffix
    suffix = str(st.session_state['form_key_suffix'])

    # 录入字段 - 使用动态 key 来确保提交后清空
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
            # 顺序: name, number, set, price, quantity, rarity, color, date, image_url
            add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            st.session_state['scrape_result'] = {}
            st.session_state['form_key_suffix'] += 1 # 递增 suffix 强制清空表单
            
            st.success(f"已录入: {name_in}")
            st.rerun() # 强制刷新并回到页面最上方
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
    df['rarity'] = df['rarity'].fillna('') 
    df['color'] = df['color'].fillna('') 
    df['card_set'] = df['card_set'].fillna('') # 确保系列不为 NaN
    df['card_number'] = df['card_number'].fillna('') # 确保编号不为 NaN
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
        # 1. 清理搜索输入
        cleaned_search_name = normalize_text_for_fuzzy_search(search_name)
        
        # 2. 对需要搜索的字段进行清理和连接
        search_target = (
            filtered_df['card_name'].astype(str).apply(normalize_text_for_fuzzy_search) + 
            filtered_df['card_number'].astype(str).apply(normalize_text_for_fuzzy_search) + 
            filtered_df['id'].astype(str).apply(normalize_text_for_fuzzy_search)
        )
        
        # 3. 执行模糊搜索 (在清理后的文本中搜索清理后的关键词)
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

    st.markdown("### 📝 数据编辑（双击单元格修改后，自动保存）") # 表示を更新
    
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

    # 【修正箇所】チェックと自動保存ロジック
    # 检查是否有编辑变动
    if st.session_state["data_editor"]["edited_rows"] or st.session_state["data_editor"]["deleted_rows"]:
        st.info("检测到数据修改，正在自动保存到 Google 表格...") 
        
        final_df_to_save = edited_df
        
        # 立即执行保存
        update_data_and_save(final_df_to_save)
        
        # 保存後に強制リロードし、最新データ（かつ非編集状態）でテーブルを再描画する
        st.rerun()

    
    st.divider()
    
    # --- ❌ 手动删除记录 (增强展示内容) ---
    st.markdown("### ❌ 手动删除记录")
    
    if not filtered_df.empty:
        # 增强删除记录的显示内容
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
            # 从选中的字符串中提取 ID
            delete_id_match = re.search(r'ID (\d+)\s*\|', selected_delete_option)
            card_id_to_delete = int(delete_id_match.group(1)) if delete_id_match else None
            
            with col_del_btn:
                 # 为了对齐，增加一个占位符
                 st.markdown("<br>", unsafe_allow_html=True)
                 if st.button("🔴 确认删除所选记录", type="secondary"):
                     if card_id_to_delete:
                         delete_card(card_id_to_delete)
                     else:
                         st.error("无法识别要删除的记录 ID。")
    else:
        st.info("没有可删除的记录。")
        
    st.divider()

    # --- 📊 单卡深度分析面板 (增强展示内容) ---
    st.markdown("### 📊 单卡深度分析")
    
    analysis_df = filtered_df.copy() 

    if analysis_df.empty:
        st.warning("无筛选结果。")
    else:
        # 使用更详细的 unique_label，包含卡名、编号、系列、等级和颜色
        analysis_df['unique_label'] = analysis_df.apply(
            lambda x: f"{x['card_name']} [{x['card_number']}] ({x['card_set']}) - {x['rarity']}/{x['color']}", 
            axis=1
        )
        
        # 下拉菜单选项 unique_variants 来自 filtered_df，只包含搜索结果。
        unique_variants = analysis_df['unique_label'].unique()
        selected_variant = st.selectbox("请选择要分析的具体卡牌:", unique_variants)
        
        # 使用选定的唯一标签进行筛选
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
                
                # 获取历史最高价及对应日期
                max_price = target_df['price'].max()
                # 找到所有匹配最高价的记录，取第一条的日期
                max_price_date = target_df[target_df['price'] == max_price]['date'].iloc[0]
                
                # 获取历史最低价及对应日期
                min_price = target_df['price'].min()
                # 找到所有匹配最低价的记录，取第一条的日期
                min_price_date = target_df[target_df['price'] == min_price]['date'].iloc[0]

                st.metric("最近成交价", f"¥{curr_price:,.0f}")
                
                # 展示最高价和最低价的录入日期
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
