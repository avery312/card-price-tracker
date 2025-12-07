import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import requests
from bs4 import BeautifulSoup
import re 
# 导入正确的 Google Sheets 连接器
import streamlit_gsheets as stg 

# === 配置 ===
# 工作表名称必须与您在 Google Sheets 中创建的标签名称一致
SHEET_NAME = "数据表" 
# 移除了 DB_NAME 和 IMAGE_FOLDER 及其相关的 os.makedirs 

# === Google Sheets 数据库函数 ===
# 移除了 get_connection(), init_db()，因为 st.connection 会处理初始化。

@st.cache_data(ttl=3600)
def load_data():
    """从 Google Sheets 读取所有数据"""
    try:
        # 使用 gsheets 连接器读取数据，连接名必须与 Secrets 中的 [gsheets] 一致
        conn = st.connection("gsheets", type=stg.GSheetsConnection)
        
        # 使用 read() 方法读取表格中的指定工作表
        df = conn.read(worksheet=SHEET_NAME, ttl="10m")
        
        # 确保列头匹配
        expected_columns = ['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url']
        if df.empty or not all(col in df.columns for col in expected_columns):
             # 如果表格为空或结构不正确，返回一个空的数据框
            return pd.DataFrame(columns=expected_columns)

        return df.sort_values(by='date', ascending=False)
    except Exception as e:
        st.error(f"无法连接或读取 Google Sheets 数据。请检查 Secrets 配置和表格授权。错误: {e}")
        return pd.DataFrame(columns=['id', 'card_name', 'card_number', 'card_set', 'rarity', 'price', 'quantity', 'date', 'image_url'])


# 更新后的 add_card 函数：直接向 Sheets 写入新行
def add_card(name, number, card_set, rarity, price, quantity, date, image_url=None):
    # 重新获取最新的数据，以便在末尾追加
    df = load_data() 
    
    # 生成新的唯一 ID
    new_id = int(df['id'].max() + 1) if not df.empty and pd.notna(df['id'].max()) else 1
    
    new_data = {
        'id': new_id,
        'card_name': name,
        'card_number': number,
        'card_set': card_set,
        'rarity': rarity,
        'price': price,
        'quantity': quantity,
        'date': date.strftime('%Y-%m-%d'), # 格式化日期以便存储
        'image_url': image_url if image_url else ""
    }
    
    # 转换为 DataFrame 才能追加
    new_df = pd.DataFrame([new_data])
    
    # 使用 append 方法追加新行到 Google Sheets
    conn = st.connection("gsheets", type=stg.GSheetsConnection)
    conn.write(worksheet=SHEET_NAME, data=new_df, ttl=0, append=True)
    
    # 写入后清除缓存，确保下次读取是最新数据
    st.cache_data.clear()


# 删除卡牌函数：通过删除行来实现
def delete_card(card_id):
    df = load_data()
    
    # 找到要删除的行索引
    row_to_delete = df[df['id'] == card_id]
    if row_to_delete.empty:
        st.error(f"ID {card_id} 的记录未找到。")
        return

    # Google Sheets 删除需要知道行号 (从 2 开始，因为第 1 行是列头)
    # 找到该 ID 在原始读取数据框中的位置，然后 +2 得到 Sheets 的行号
    # 注意：这里需要找到原始的索引位置，而不是 df.index.get_loc()

    # 找到该行在 Sheets 中的物理行号 (Header is row 1, data starts at row 2)
    # Streamlit GSheets Connection 通常基于 Pandas 索引来实现删除
    
    # 简单起见，我们重写整个数据框以排除该行（如果数据量不大，此方法可靠）
    df_updated = df[df['id'] != card_id]
    
    conn = st.connection("gsheets", type=stg.GSheetsConnection)
    # 使用 write 覆盖整个工作表，只保留需要的数据
    conn.write(worksheet=SHEET_NAME, data=df_updated.drop(columns=['date_dt'], errors='ignore'), ttl=0, header=True)
    
    st.cache_data.clear()
    

# 移除了 save_uploaded_image 函数 (本地文件操作)

# 🌟 网页抓取函数 (保留，但移除了 @st.cache_data 以防与 load_data 缓存冲突)
def scrape_card_data(url):
    # 代码内容不变...
    st.info(f"正在尝试从 {url} 抓取数据...")
    if not url.startswith("http"):
        return {"error": "网址格式不正确，必须以 http 或 https 开头。"}
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() 
        response.encoding = 'EUC-JP'
        soup = BeautifulSoup(response.content, 'html.parser')

        # --- 1. 提取 主标题行 (包含 等级, 名称, 版本) ---
        YUYU_TEI_BASE_IMAGE_URL = 'https://card.yuyu-tei.jp/opc/front/' 
        
        name_tag = soup.select_one('h1')
        full_title = name_tag.get_text(strip=True) if name_tag else ""
        
        card_name = "N/A"
        card_rarity = "N/A"
        card_set = "N/A"
        
        if full_title:
            rarity_match = re.match(r'^([A-Z0-9\-]+)', full_title)
            if rarity_match:
                card_rarity = rarity_match.group(1).strip()
                remainder = full_title[len(rarity_match.group(0)):].strip()
            else:
                remainder = full_title
                card_rarity = "N/A"
            
            name_match = re.match(r'([^(\s]+)', remainder)
            if name_match:
                card_name = name_match.group(1).strip()
            else:
                card_name = remainder.strip() 

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
# init_db() 已删除

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
    
    price_in = st.number_input("5. 价格 (¥)", min_value=0.0, step=10.0)
    quantity_in = st.number_input("6. 数量 (张)", min_value=1, step=1)
    
    date_in = st.date_input("7. 录入日期", datetime.now())

    st.divider()
    st.write("🖼️ 卡牌图片 (可修正)")
    # 移除了本地上传选项
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
    
    # 确定最终图片路径，对于云端部署，只能是 URL
    if img_source == "网络链接":
        final_image_path = image_url_input
    else:
        final_image_path = None

    if st.button("提交录入", type="primary"):
        if name_in:
            # 移除了 save_uploaded_image 逻辑
            
            # 调用新的 add_card 函数 (写入 Google Sheets)
            add_card(name_in, card_number_in, set_in, rarity_in, price_in, quantity_in, date_in, final_image_path)
            
            st.session_state['scrape_result'] = {}
            st.success(f"已录入: {name_in} - ¥{price_in} x {quantity_in} 张")
            st.rerun()
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

# 调用新的 load_data 函数
df = load_data()

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据。")
else:
    # 预处理
    # 确保 id 列是数字类型，防止出现浮点数
    df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int) 
    df['date_dt'] = pd.to_datetime(df['date'], errors='coerce')
    df['image_url'] = df['image_url'].fillna('')
    df['rarity'] = df['rarity'].fillna('')
    df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(1).astype(int) 
    df = df.dropna(subset=['date_dt']) # 删除日期无效的行，避免崩溃

    # --- 🔍 多维度筛选 ---
    st.markdown("### 🔍 多维度筛选")
    col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
    with col_s1: search_name = st.text_input("搜索 名称 (模糊)")
    with col_s2: search_number = st.text_input("搜索 编号 (模糊)")
    with col_s3: search_set = st.text_input("搜索 系列/版本 (模糊)")
    with col_s4: search_rarity = st.text_input("搜索 等级 (模糊)")
    with col_s5: date_range = st.date_input("搜索 时间范围", value=[], help="请选择开始和结束日期")

    # 筛选逻辑 (略过此处，与原逻辑相同)
    filtered_df = df.copy()
    if search_name:
        filtered_df = filtered_df[filtered_df['card_name'].str.contains(search_name, case=False, na=False)]
    if search_number:
        filtered_df = filtered_df[filtered_df['card_number'].str.contains(search_number, case=False, na=False)]
    if search_set:
        filtered_df = filtered_df[filtered_df['card_set'].str.contains(search_set, case=False, na=False)]
    if search_rarity:
        filtered_df = filtered_df[filtered_df['rarity'].str.contains(search_rarity, case=False, na=False)]
    if len(date_range) == 2:
        # 将 date_dt 转换为 date 类型进行比较
        filtered_df = filtered_df[(filtered_df['date_dt'].dt.date >= date_range[0]) & (filtered_df['date_dt'].dt.date <= date_range[1])]

    # 展示筛选后的表格 
    # 确保在展示前移除 date_dt，保留 date (TEXT)
    display_df = filtered_df.drop(columns=['date_dt', 'id'], errors='ignore')

    st.dataframe(
        display_df, 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "image_url": st.column_config.ImageColumn(
                "图片预览 (点击打开大图)", help="图片，点击后在新窗口打开", width="small"
            ),
            "price": st.column_config.NumberColumn(
                "价格 (¥)", format="¥%d"
            ),
             "quantity": st.column_config.NumberColumn(
                "数量 (张)", format="%d"
            )
        } 
    )

    st.divider()

    # --- 📊 深度分析面板 ---
    st.markdown("### 📊 单卡深度分析")
    if filtered_df.empty:
        st.warning("无筛选结果。")
    else:
        filtered_df['unique_label'] = filtered_df['card_name'] + " [" + filtered_df['card_number'] + " " + filtered_df['rarity'] + "]"
        unique_variants = filtered_df['unique_label'].unique()
        selected_variant = st.selectbox("请选择要分析的具体卡牌:", unique_variants)
        
        target_df = filtered_df[filtered_df['unique_label'] == selected_variant].sort_values("date_dt")
        
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
            curr_price = target_df.iloc[-1]['price']
            total_quantity = target_df['quantity'].sum()
            
            st.metric("最近成交价", f"¥{curr_price:,.0f}")
            st.metric("📈 历史最高 / 📉 最低", f"¥{target_df['price'].max():,.0f} / ¥{target_df['price'].min():,.0f}")
            st.metric("💰 平均价格", f"¥{target_df['price'].mean():,.2f}")
            st.metric("📦 总库存数量", f"{total_quantity:,} 张")
            st.write(f"共 {len(target_df)} 条记录")

        with col_chart:
            st.caption("价格走势图")
            if len(target_df) > 1:
                st.line_chart(target_df, x="date", y="price", color="#FF4B4B")
            else:
                st.info("需至少两条记录绘制走势")

    # --- 🗑️ 数据管理 ---
    with st.expander("🗑️ 数据管理 (删除记录)"):
        if not filtered_df.empty:
            filtered_df['del_label'] = filtered_df.apply(lambda x: f"ID:{x['id']} | {x['date']} | {x['card_name']} ({x['card_number']}) | ¥{x['price']} x {x['quantity']}", axis=1)
            del_select = st.selectbox("选择要删除的记录:", filtered_df['del_label'])
            if st.button("确认删除选中记录"):
                # 安全地提取 ID，避免类型错误
                try:
                    del_id = int(del_select.split("|")[0].replace("ID:", "").strip())
                    delete_card(del_id)
                    st.success("已删除！请等待应用自动刷新。")
                except Exception as e:
                    st.error(f"删除失败，请检查 ID 格式。错误: {e}")
                
                # 延迟重跑以等待 Sheets 更新完成
                st.balloons()
                st.rerun()
