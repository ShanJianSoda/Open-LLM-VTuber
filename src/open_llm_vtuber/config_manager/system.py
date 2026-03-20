# config_manager/system.py
from pydantic import Field, model_validator
from typing import Dict, ClassVar, Optional
from .i18n import I18nMixin, Description


class SystemConfig(I18nMixin):
    """System configuration settings."""

    conf_version: str = Field(..., alias="conf_version")
    host: str = Field(..., alias="host")
    port: int = Field(..., alias="port")
    config_alts_dir: str = Field(..., alias="config_alts_dir")
    tool_prompts: Dict[str, str] = Field(..., alias="tool_prompts")
    enable_proxy: bool = Field(False, alias="enable_proxy")
    # MaiBot 桥接：启用后用户输入会转发到 MaiBot message_process（不携带历史），回复由 MaiBot 返回
    maibot_enabled: bool = Field(False, alias="maibot_enabled")
    maibot_host: str = Field("localhost", alias="maibot_host")
    maibot_port: int = Field(18000, alias="maibot_port")


    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "conf_version": Description(en="Configuration version", zh="配置文件版本"),
        "host": Description(en="Server host address", zh="服务器主机地址"),
        "port": Description(en="Server port number", zh="服务器端口号"),
        "config_alts_dir": Description(
            en="Directory for alternative configurations", zh="备用配置目录"
        ),
        "tool_prompts": Description(
            en="Tool prompts to be inserted into persona prompt",
            zh="要插入到角色提示词中的工具提示词",
        ),
        "enable_proxy": Description(
            en="Enable proxy mode for multiple clients",
            zh="启用代理模式以支持多个客户端使用一个 ws 连接",
        ),
        "maibot_enabled": Description(en="Use MaiBot for reply (no history sent)", zh="使用 MaiBot 回复（不传历史）"),
        "maibot_host": Description(en="MaiBot WebSocket host", zh="MaiBot WebSocket 主机"),
        "maibot_port": Description(en="MaiBot WebSocket port", zh="MaiBot WebSocket 端口"),
    }

    @model_validator(mode="after")
    def check_port(cls, values):
        port = values.port
        if port < 0 or port > 65535:
            raise ValueError("Port must be between 0 and 65535")
        return values
