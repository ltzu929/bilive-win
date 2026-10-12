"""Current creator topic/collection protocols; never submit a video here."""
from __future__ import annotations

class PublicationOptionsError(ValueError):
    pass


def validate_selections(topic=None, season=None):
    for value, keys, label in ((topic, ('id', 'mission_id'), '话题'),
                               (season, ('id', 'section_id'), '合集')):
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f'{label}选择无效')
        for key in keys:
            minimum = 0 if key == 'mission_id' else 1
            if type(value.get(key)) is not int or value[key] < minimum:
                raise ValueError(f'{label}标识无效')
    if topic is not None and (not isinstance(topic.get('name'), str) or not topic['name'].strip()):
        raise ValueError('话题名称缺失')
    if topic is not None and any(c in topic['name'] for c in ',，\n\r'):
        raise ValueError('话题名称不能包含标签分隔符')


class PublicationOptionsClient:
    def __init__(self, session, csrf):
        self.session, self.csrf = session, csrf

    def request(self, path, *, params=None, body=None, allow_missing=False):
        import requests
        try:
            url = 'https://member.bilibili.com' + path
            response = (self.session.get(url, params=params, timeout=20) if body is None else
                        self.session.post(url, params={'csrf': self.csrf}, json=body, timeout=20))
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise PublicationOptionsError('B 站话题/合集接口请求失败，请检查登录或稍后重试') from None
        if allow_missing and isinstance(payload, dict) and payload.get('code') == -404:
            return {}
        if not isinstance(payload, dict) or payload.get('code') != 0:
            code = payload.get('code') if isinstance(payload, dict) else '格式变化'
            raise PublicationOptionsError(f'B 站话题/合集接口失败（错误码 {code}）')
        data = payload.get('data')
        if body is not None:
            return data if isinstance(data, dict) else {}
        if not isinstance(data, dict):
            raise PublicationOptionsError('B 站话题/合集返回结构变化')
        return data

    def search_topics(self, keywords, offset=0):
        data = self.request('/x/vupre/web/topic/search', params={
            'keywords': keywords, 'offset': offset, 'page_size': 20})
        result = data.get('result')
        if not isinstance(result, dict) or not isinstance(result.get('topics'), list):
            raise PublicationOptionsError('B 站话题列表结构变化')
        items = []
        for row in result['topics']:
            topic = {'id': row.get('id'), 'name': row.get('name'), 'mission_id': row.get('mission_id')}
            validate_selections(topic)
            items.append({**topic, 'description': str(row.get('description') or ''),
                          'activity': str(row.get('activity_sign') or ''),
                          'activity_description': str(row.get('act_protocol') or '')})
        page = result.get('page_info') or {}
        return {'items': items, 'has_more': bool(page.get('has_more')), 'offset': page.get('offset'),
                'tips': str(result.get('tips') or '')}

    def topic_info(self, topic):
        validate_selections(topic)
        data = self.request('/x/vupre/web/topic/info', params={'topic_id': topic['id']})
        if data.get('id') != topic['id'] or data.get('is_valid') is not True:
            raise PublicationOptionsError('所选话题已失效，请重新选择')
        if data.get('mission_id') != topic['mission_id'] or data.get('name') != topic['name']:
            raise PublicationOptionsError('所选话题或活动信息已变化，请重新选择')
        if topic['mission_id'] and data.get('mission_state') != 1:
            raise PublicationOptionsError('所选活动当前不可参与，请重新选择')
        return {'id': data['id'], 'name': data['name'], 'mission_id': data['mission_id'],
                'description': str(data.get('topic_description') or ''),
                'activity': str(data.get('activity_text') or ''),
                'activity_description': str(data.get('activity_description') or ''),
                'tips': str(data.get('tips') or '')}

    def list_seasons(self):
        items, page = [], 1
        while True:
            data = self.request('/x2/creative/web/seasons', params={'pn': page, 'ps': 30})
            rows, total = data.get('seasons'), data.get('total')
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise PublicationOptionsError('B 站合集列表结构变化')
            for row in rows:
                season = row.get('season') or {}
                sections = (row.get('sections') or {}).get('sections')
                if not isinstance(sections, list):
                    raise PublicationOptionsError('B 站合集小节结构变化')
                # Paid, forbidden and unapproved collections cannot be chosen.
                if season.get('state') != 0 or season.get('forbid') or season.get('is_pay'):
                    continue
                for section in sections:
                    if section.get('state') != 0:
                        continue
                    item = {'id': season.get('id'), 'title': str(season.get('title') or ''),
                            'section_id': section.get('id'), 'section_title': str(section.get('title') or '')}
                    validate_selections(season=item)
                    if section.get('seasonId') != item['id']:
                        raise PublicationOptionsError('B 站合集小节归属变化')
                    items.append(item)
            if page * 30 >= total:
                return {'items': items}
            if len(rows) != 30:
                raise PublicationOptionsError('B 站合集分页不完整')
            page += 1

    def validate_options(self, topic=None, season=None):
        validate_selections(topic, season)
        result = {'topic': self.topic_info(topic) if topic else None, 'season': None}
        if season:
            match = next((s for s in self.list_seasons()['items'] if
                          (s['id'], s['section_id']) == (season['id'], season['section_id'])), None)
            if match is None:
                raise PublicationOptionsError('所选合集或小节已不可用，请重新选择')
            result['season'] = match
        return result

    def add_to_season(self, aid, bvid, title, season):
        self.validate_options(season=season)
        if type(aid) is not int or aid <= 0:
            raise PublicationOptionsError('投稿已成功但缺少稿件 ID，请到创作中心加入合集')
        view = self.request('/x/vupre/web/archive/view', params={'aid': aid})
        archive, videos = view.get('archive') or {}, view.get('videos')
        if archive.get('aid') != aid or archive.get('bvid') != bvid or not isinstance(videos, list) or len(videos) != 1:
            raise PublicationOptionsError('投稿稿件信息尚不可用或不匹配，请稍后重试加入合集')
        cid = videos[0].get('cid')
        if type(cid) is not int or cid <= 0:
            raise PublicationOptionsError('稿件尚未返回视频 ID，请稍后重试加入合集')
        # An interrupted response must not cause a duplicate collection add.
        current = self.request('/x2/creative/web/season/aid', params={'id': aid}, allow_missing=True)
        if current.get('id') == season['id']:
            return
        if current.get('id'):
            raise PublicationOptionsError('稿件已加入其他合集，请到创作中心核对')
        self.request('/x2/creative/web/season/section/episodes/add', body={
            'sectionId': season['section_id'], 'episodes': [{'aid': aid, 'cid': cid, 'title': title}]})
        current = self.request('/x2/creative/web/season/aid', params={'id': aid}, allow_missing=True)
        if current.get('id') != season['id']:
            raise PublicationOptionsError('合集接口已返回成功，但关联尚未确认，请稍后重试或到创作中心核对')
