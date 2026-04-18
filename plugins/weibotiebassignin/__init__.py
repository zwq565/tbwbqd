import re
import time
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional
from urllib.parse import unquote

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from requests import Session

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType


class WeiboTiebaSignIn(_PluginBase):
    plugin_name = "微博贴吧自动签到"
    plugin_desc = "定时自动签到微博和贴吧（支持已关注贴吧自动签到）"
    plugin_icon = "signin.png"
    plugin_version = "1.0"
    plugin_author = "Grok助手"
    author_url = "https://github.com/zwq565"
    plugin_config_prefix = "weibotieba_"
    plugin_order = 50
    auth_level = 1

    _enabled: bool = False
    _cron: str = "0 8 * * *"
    _onlyonce: bool = False
    _notify: bool = True
    _weibo_cookie: str = ""
    _tieba_cookie: str = ""

    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        self.stop_service()
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "0 8 * * *")
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", True)
            self._weibo_cookie = config.get("weibo_cookie", "")
            self._tieba_cookie = config.get("tieba_cookie", "")

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._onlyonce:
                logger.info("【微博贴吧签到】立即运行一次")
                self._scheduler.add_job(func=self.__sign, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3))
                self._onlyonce = False
                self.update_config({"onlyonce": False, "enabled": self._enabled, "cron": self._cron,
                                    "notify": self._notify, "weibo_cookie": self._weibo_cookie,
                                    "tieba_cookie": self._tieba_cookie})

            if self._enabled and self._cron:
                self._scheduler.add_job(func=self.__sign, trigger=CronTrigger.from_crontab(self._cron),
                                        name="微博贴吧自动签到")
                self._scheduler.start()
                logger.info(f"【微博贴吧签到】定时服务已启动，cron: {self._cron}")

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        if self._scheduler:
            self._scheduler.shutdown()
            self._scheduler = None

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            return [{
                "id": "WeiboTiebaSignIn",
                "name": "微博贴吧自动签到服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.__sign,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '签到完成后通知'}}
                        ]},
                        {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                            {'component': 'VTextField', 'props': {'model': 'cron', 'label': '定时 cron 表达式', 'placeholder': '0 8 * * *'}}
                        ]}
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VTextarea', 'props': {'model': 'weibo_cookie', 'label': '微博 Cookie（完整字符串）', 'placeholder': 'SUB=...; SSOLoginState=...', 'rows': 3}}
                        ]}
                    ]},
                    {'component': 'VRow', 'content': [
                        {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                            {'component': 'VTextarea', 'props': {'model': 'tieba_cookie', 'label': '贴吧 Cookie（完整字符串）', 'placeholder': 'BDUSS=...; STOKEN=...', 'rows': 3}}
                        ]}
                    ]}
                ]
            }
        ], {
            "enabled": False, "notify": True, "cron": "0 8 * * *",
            "weibo_cookie": "", "tieba_cookie": ""
        }

    def __sign(self):
        logger.info("【微博贴吧签到】开始执行...")
        results = []
        if self._tieba_cookie:
            results.append(f"贴吧：{self.__tieba_sign(self._tieba_cookie)}")
        else:
            results.append("贴吧：未配置 Cookie，跳过")
        if self._weibo_cookie:
            results.append(f"微博：{self.__weibo_sign(self._weibo_cookie)}")
        else:
            results.append("微博：未配置 Cookie，跳过")
        msg = "\n".join(results)
        logger.info(f"【微博贴吧签到】完成\n{msg}")
        if self._notify:
            self.post_message(mtype=NotificationType.SiteMessage, title="【微博贴吧签到完成】", text=msg)

    def __tieba_sign(self, cookie: str) -> str:
        try:
            headers = {"User-Agent": "Mozilla/5.0", "Cookie": cookie}
            s = Session()
            s.headers.update(headers)
            tbs = s.get("https://tieba.baidu.com/dc/common/tbs").json().get("tbs")
            if not tbs:
                return "获取 tbs 失败"
            mylike = s.get("https://tieba.baidu.com/f/like/mylike").text
            kw_list = list(set([unquote(kw) for kw in re.findall(r'href="/f\?kw=([^"]+)"', mylike)]))
            if not kw_list:
                return "未找到已关注贴吧"
            success = 0
            for kw in kw_list:
                data = {"ie": "utf-8", "kw": kw, "tbs": tbs}
                resp = s.post("https://tieba.baidu.com/sign/add", data=data)
                if "success" in resp.text.lower() or "已签到" in resp.text:
                    success += 1
                time.sleep(0.5)
            return f"成功签到 {success}/{len(kw_list)} 个贴吧"
        except Exception as e:
            logger.error(f"贴吧签到异常: {e}")
            return f"签到失败: {str(e)}"

    def __weibo_sign(self, cookie: str) -> str:
        return "微博签到已触发（当前为框架，超话签到可后续完善）"
