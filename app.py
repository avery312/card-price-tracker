import streamlit as st
import pandas as pd
from datetime import datetime
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
# 移除了 data_version 变量

def clear_all_data():
    st.session_state['scrape_result'] = {} 
    st.session_state['form_key_suffix'] += 1 

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

# 🔑 关键修复：load_data 不再使用 @st.cache_data
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

# 处理数据编辑器的内容并保存到 Supabase
def update_data_and_save(edited_df):
    supabase = connect_supabase()
    if not supabase: return
    
    try:
        # 1. 数据类型清理和格式化
        edited_df['date'] = pd.to_datetime(edited_df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
        edited_df['id'] = pd.to_numeric(edited_df['id'], errors='coerce').fillna(0).astype(int)
        edited_df['price'] = pd.to_numeric(edited_df['price'], errors='coerce').fillna(0)
        edited_df['quantity'] = pd.to_numeric(edited_df['quantity'], errors='coerce').fillna(0).astype(int)
        
        df_final = edited_df[NEW_EXPECTED_COLUMNS].fillna('')
        data_to_save = df_final.to_dict('records')

        # 2. 核心操作：删除所有旧数据，然后重新插入所有新数据
        supabase.table(SUPABASE_TABLE_NAME).delete().neq('id', 0).execute() 

        if data_to_save:
            supabase.table(SUPABASE_TABLE_NAME).insert(data_to_save).execute()
        
        st.success("数据修改已即时保存到 Supabase！")
    except Exception as e:
        st.error(f"保存修改失败。错误: {e}")


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

    
    # 🔑 关键修复：使用 st.form 包裹手动输入和提交按钮
    # 使用唯一的 key 确保表单不会因 session state 变化而混淆
    with st.form(key=f"manual_entry_form_{suffix}"):
        card_number_in = st.text_input("1. 卡牌编号", value=number_default, key=f"card_number_in_form_{suffix}")
        name_in = st.text_input("2. 卡牌名称 (必填)", value=name_default, key=f"name_in_form_{suffix}")
        set_in = st.text_input("3. 系列/版本", value=set_default, key=f"set_in_form_{suffix}") 
        rarity_in = st.text_input("4. 等级 (Rarity)", value=rarity_default, key=f"rarity_in_form_{suffix}") 
        color_in = st.text_input("5. 颜色 (例如: 紫)", value=color_default, key=f"color_in_form_{suffix}") 
        
        price_in = st.number_input("6. 价格 (¥)", min_value=0.0, step=10.0, key=f"price_in_form_{suffix}")
        quantity_in = st.number_input("7. 数量 (张)", min_value=1, step=1, key=f"quantity_in_form_{suffix}")
        
        date_in = st.date_input("8. 录入日期", datetime.now(), key=f"date_in_form_{suffix}")

        st.divider()
        st.write("🖼️ 卡牌图片 (可修正)")

        image_url_input = st.text_input("输入图片网址 (URL)", value=img_url_default, key=f"image_url_input_form_{suffix}")
        final_image_path = image_url_input if image_url_input else None
        
        if final_image_path:
            try:
                st.image(final_image_path, caption="预览", use_container_width=True)
            except: 
                st.warning("无法加载该链接的图片。")

        # 使用 st.form_submit_button 替换 st.button
        submitted = st.form_submit_button("提交录入", type="primary")

    if submitted:
        if name_in:
            with st.spinner("🚀 数据即时保存中..."):
                add_card(name_in, card_number_in, set_in, price_in, quantity_in, rarity_in, color_in, date_in, final_image_path)
            
            # 清除侧边栏输入状态
            st.session_state['scrape_result'] = {}
            st.session_state['form_key_suffix'] += 1
            
            st.success(f"✅ 已成功录入: {name_in}")
            # **新增/修改**：增加短暂延迟，确保 Streamlit 渲染成功提示，并触发从顶部的脚本重新运行
            time.sleep(0.5) 
            
            # 强制重新执行脚本
            st.rerun() 
        else:
            st.error("卡牌名称不能为空！")

# --- 主页面 ---
st.title("📈 卡牌历史与价格分析 Pro")

# 🔑 load_data() 每次 rerun 都会执行数据库读取
df = load_data() 

if df.empty:
    st.info("👋 欢迎！请在左侧录入你的第一张卡牌数据。")
else:
    # 预处理 (与之前代码保持一致)
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
    with col_s1: search_name = st.text_input("搜索 名称/编号/ID", help="支持模糊搜索") 
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
    # 确保 data_editor 的 date 列为 date 对象
    display_df['date'] = pd.to_datetime(display_df['date'], errors='coerce').dt.date 

    # 核心排序逻辑：根据 ID 从大到小（最新的在最上面）进行初始排序
    display_df = display_df.sort_values(by='id', ascending=False)
    
    st.markdown("### 📝 数据编辑（双击单元格修改、支持多行删除）")
    st.caption("ℹ️ **删除提示**：请选中要删除的行，然后按键盘上的 **`Delete`** 键（或使用右上角的菜单）进行多行删除。删除后请点击下方的 **保存** 按钮。")
    
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
    }
    
    edited_df = st.data_editor(
        display_df,
        key="data_editor",
        use_container_width=True, 
        hide_index=True,
        column_order=['id'] + FINAL_DISPLAY_COLUMNS,
        column_config=column_config_dict,
        num_rows="dynamic",
    )

    # 检查是否有编辑变动或删除操作
    if st.session_state["data_editor"]["edited_rows"] or st.session_state["data_editor"]["deleted_rows"]:
        st.warning("⚠️ 数据修改或删除操作已检测到。请点击 **保存修改** 按钮！")
        
        final_df_to_save = edited_df
        
        if st.button("💾 确认并保存所有修改", type="primary"):
            with st.spinner("🚀 数据即时保存中..."):
                update_data_and_save(final_df_to_save)
            # 强制重新执行脚本
            st.rerun()

    
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
                st.line_chart(target_df, x="date_dt", y="price", color="#FF4B4B")
            else:
                st.info("需至少两条记录绘制走势")
    
    # --- 📥 数据导出 (用于备份或迁移) --- (已移动至最底部)
    st.divider()
    st.markdown("### 📥 数据导出 (用于备份或迁移)")
    if not df.empty:
        csv_data = df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        st.download_button(
            label="下载完整的卡牌数据 (CSV)",
            data=csv_data,
            file_name='card_data_full_export.csv',
            mime='text/csv',
            help="点击下载 Supabase 中的所有数据，用于备份。"
        )
