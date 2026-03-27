import re
import time
import requests
import logging
import threading
from bs4 import BeautifulSoup
from db_manager import DBManager, DOUBAN_CACHE_DB
from .base_processor import BaseRssProcessor

logger = logging.getLogger(__name__)

# 豆瓣 API 的速率限制
DOUBAN_API_RATE_LIMIT = 2  # 秒

# 进程级全局限速（避免每次刷新新建 Processor 导致限速失效）
_douban_rate_lock = threading.Lock()
_douban_last_call_time = 0.0

class DoubanProcessor(BaseRssProcessor):
    def __init__(self, vlib):
        super().__init__(vlib)
        self.douban_db = DBManager(DOUBAN_CACHE_DB)

    def _parse_source_ids(self, xml_content):
        """从 RSS XML 中解析出豆瓣 ID、标题和年份。如果ID不存在，则尝试从描述中解析。"""
        soup = BeautifulSoup(xml_content, 'xml')
        items_data = []
        for item in soup.find_all('item'):
            link_tag = item.find('link')
            link = link_tag.text if link_tag else ''
            title_text = item.find('title').text
            
            douban_id_match = re.search(r'douban.com/subject/(\d+)', link)
            if not douban_id_match:
                douban_id_match = re.search(r'douban.com/doubanapp/dispatch/movie/(\d+)', link)
            
            if douban_id_match:
                douban_id = douban_id_match.group(1)
                
                # 尝试从标题中解析年份
                year_match = re.search(r'\((\d{4})\)', title_text)
                year = int(year_match.group(1)) if year_match else None
                
                # 清理标题
                title = re.sub(r'\(\d{4}\)', '', title_text).strip()
                
                items_data.append({
                    "id": douban_id,
                    "title": title,
                    "year": year
                })
            else:
                # 如果没有豆瓣ID，尝试从 description 中解析
                logger.info(f"条目 '{title_text}' 缺少豆瓣ID，尝试从描述中解析。")
                description_tag = item.find('description')
                if not description_tag:
                    logger.warning(f"条目 '{title_text}' 既无豆瓣ID也无描述，已跳过。")
                    continue

                description_html = description_tag.text
                desc_soup = BeautifulSoup(description_html, 'html.parser')
                p_tags = desc_soup.find_all('p')
                
                year = None
                # 豆瓣RSSHub格式通常是：<p>标题</p><p>评分</p><p>年份 / 国家 / ...</p>
                if len(p_tags) > 1:
                    # 年份信息通常在最后一个p标签
                    info_line = p_tags[-1].text
                    year_match = re.search(r'^\s*(\d{4})', info_line)
                    if year_match:
                        year = int(year_match.group(1))

                title = title_text.strip()
                
                items_data.append({
                    "id": None,  # id为None以触发兜底匹配
                    "title": title,
                    "year": year
                })
                if year:
                    logger.info(f"成功从描述中解析出年份: {year}，标题: {title}")
                else:
                    logger.warning(f"未能从描述中解析出年份，标题: {title}")

        logger.info(f"从 RSS 源中共解析出 {len(items_data)} 个条目。")
        return items_data

    def _get_tmdb_info(self, source_info):
        """通过豆瓣ID获取TMDB信息，返回一个包含 (tmdb_id, media_type) 元组的列表"""
        douban_id = source_info['id']
        # 检查是否已存在 豆瓣ID -> TMDB ID 的映射
        existing_mapping = self.douban_db.fetchone(
            "SELECT tmdb_id, media_type FROM douban_tmdb_mapping WHERE douban_id = ?",
            (douban_id,)
        )
        if existing_mapping:
            tmdb_id = existing_mapping['tmdb_id']
            media_type = existing_mapping['media_type']
            logger.info(f"豆瓣ID {douban_id}: 在缓存中找到已存在的TMDB映射 -> {tmdb_id} ({media_type})")
            return [(tmdb_id, media_type)]

        # 如果没有映射，则走完整流程
        logger.info(f"豆瓣ID {douban_id}: 未找到TMDB映射，开始完整处理流程。")
        imdb_id = self._get_imdb_id_from_douban_page(douban_id)
        if not imdb_id:
            logger.warning(f"豆瓣ID {douban_id}: 因未能找到 IMDb ID 而跳过。")
            return []

        tmdb_id, media_type = self._get_tmdb_info_from_imdb(imdb_id)
        if not tmdb_id or not media_type:
            return []
        
        # 存入映射关系
        self.douban_db.execute(
            "INSERT OR REPLACE INTO douban_tmdb_mapping (douban_id, tmdb_id, media_type, match_method) VALUES (?, ?, ?, ?)",
            (douban_id, tmdb_id, media_type, 'douban_id'),
            commit=True
        )
        return [(tmdb_id, media_type)]

    def _get_imdb_id_from_douban_page(self, douban_id):
        """通过访问豆瓣页面抓取 IMDb ID 和标题"""
        cached = self.douban_db.fetchone("SELECT api_response, name FROM douban_api_cache WHERE douban_id = ?", (f"douban_imdb_{douban_id}",))
        if cached and cached['api_response']:
            logger.info(f"豆瓣ID {douban_id}: 在缓存中找到 IMDb ID -> {cached['api_response']} (名称: {cached['name'] or 'N/A'})")
            return cached['api_response']

        global _douban_last_call_time
        with _douban_rate_lock:
            since_last_call = time.time() - _douban_last_call_time
            if since_last_call < DOUBAN_API_RATE_LIMIT:
                sleep_time = DOUBAN_API_RATE_LIMIT - since_last_call
                logger.info(f"豆瓣页面访问速率限制：休眠 {sleep_time:.2f} 秒。")
                time.sleep(sleep_time)
            _douban_last_call_time = time.time()

        url = f"https://movie.douban.com/subject/{douban_id}/"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'}
        
        logger.info(f"豆瓣ID {douban_id}: 正在抓取页面 {url} 以寻找 IMDb ID。")
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"抓取豆瓣页面失败 (ID: {douban_id}): {e}")
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title_tag = soup.find('span', property='v:itemreviewed')
        name = title_tag.text.strip() if title_tag else None
        
        imdb_id_match = re.search(r'IMDb:</span> (tt\d+)', response.text)
        imdb_id = imdb_id_match.group(1) if imdb_id_match else None
        
        if imdb_id:
            logger.info(f"豆瓣ID {douban_id}: 从页面中找到 IMDb ID {imdb_id} (名称: {name})。")
        else:
            logger.warning(f"豆瓣ID {douban_id}: 在页面中未能找到 IMDb ID。")

        self.douban_db.execute(
            "INSERT OR REPLACE INTO douban_api_cache (douban_id, api_response, name) VALUES (?, ?, ?)",
            (f"douban_imdb_{douban_id}", imdb_id or '', name),
            commit=True
        )
        return imdb_id

    def _get_tmdb_info_from_imdb(self, imdb_id):
        """通过 IMDb ID 查询 TMDB"""
        logger.info(f"IMDb ID {imdb_id}: 正在查询 TMDB API...")
        tmdb_api_key = self.config.tmdb_api_key
        if not tmdb_api_key:
            raise ValueError("TMDB API Key not configured.")

        url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={tmdb_api_key}&external_source=imdb_id"
        
        proxies = {"http": self.config.tmdb_proxy, "https": self.config.tmdb_proxy} if self.config.tmdb_proxy else None
        
        response = requests.get(url, proxies=proxies)
        response.raise_for_status()
        data = response.json()

        if data.get('movie_results'):
            tmdb_id = data['movie_results'][0]['id']
            media_type = 'movie'
        elif data.get('tv_results'):
            tmdb_id = data['tv_results'][0]['id']
            media_type = 'tv'
        else:
            logger.warning(f"IMDb ID {imdb_id}: 在 TMDB 上未找到结果。")
            return None, None
        
        logger.info(f"IMDb ID {imdb_id}: 在 TMDB 上找到 -> TMDB ID: {tmdb_id}, 类型: {media_type}")
        return str(tmdb_id), media_type
