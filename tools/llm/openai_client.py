import json
from openai import OpenAI,AsyncOpenAI
from sse_starlette.sse import EventSourceResponse
import base64
import mimetypes
from typing import Dict, Any, List, Union
import os
from constant import ABS_DIR

class OpenaiLLMClient:
    def __init__(self, llm_config: Dict[str, Any]):
        """初始化客户端
        :param llm_config: 必须包含以下参数：
            model: 模型标识
            base_url: API端点
            api_key: API密钥
        """
        # 参数验证
        required_keys = {'model', 'base_url', 'api_key'}
        if missing := required_keys - llm_config.keys():
            raise ValueError(f"缺少必要配置参数: {missing}")
        # 核心参数
        self.model = llm_config.get('model', "deepseek-chat")
        self.base_url = llm_config.get('base_url', "https://api.deepseek.com")
        self.api_key = llm_config.get('api_key', "sk-d499834787934fa3a654cb13f9a62fb9")
        # 模型参数配置
        self.llm_params = llm_config.get("params", {})
        self.params_config = {
            "temperature": self.llm_params.get('temperature', 0.6),
            "presence_penalty": self.llm_params.get('presence_penalty', 0.0),
            "frequency_penalty": self.llm_params.get('frequency_penalty', 0.0),
            "top_p": self.llm_params.get('top_p', 1.0),
            "seed": self.llm_params.get('seed', 42),
            "max_tokens": self.llm_params.get('max_tokens', 2048)
        }
        # 初始化同步客户端
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )
    def close(self):
        """关闭客户端连接"""
        if hasattr(self, '_client'):
            self._client.close()

    def generate(self, messages) -> Dict[str, Any]:
        """生成非流式响应
        :param prompt: 用户输入的提示词
        :return: 包含响应内容和元数据的字典
        """
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                **self.params_config
            )

            return {
                "success": True,
                "data": {
                    "content": response.choices[0].message.content,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": {
                    "code": 500,
                    "message": str(e)
                }
            }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class OpenaiLLMImageClient:
    def __init__(self, llm_config: Dict[str, Any]):
        """
        初始化图像多模态客户端
        :param llm_config: 必须包含以下参数：
            model: 模型标识（如 gpt-4o）
            base_url: API端点
            api_key: API密钥
        """
        required_keys = {'model', 'base_url', 'api_key'}
        if missing := required_keys - llm_config.keys():
            raise ValueError(f"缺少必要配置参数: {missing}")

        self.model = llm_config['model']
        self.base_url = llm_config['base_url']
        self.api_key = llm_config['api_key']
        self.system_prompt = llm_config.get('system_prompt', "你是一个图像理解助手。")
        self.llm_params = llm_config.get("params", {})
        self.params_config = {
            "temperature": self.llm_params.get('temperature', 0.6),
            "top_p": self.llm_params.get('top_p', 1.0),
            "max_tokens": self.llm_params.get('max_tokens', 1024),

        }
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )

    def close(self):
        if hasattr(self, '_client'):
            self._client.close()

    def _is_url(self, path: str) -> bool:
        return path.startswith("http://") or path.startswith("https://")

    def _encode_image_to_base64(self, image_path: str) -> str:
        """将本地图片转为 base64 格式的 data:image/..."""
        mime_type, _ = mimetypes.guess_type(image_path)
        if not mime_type or not mime_type.startswith("image/"):
            raise ValueError(f"无法识别图片 MIME 类型: {image_path}")

        with open(image_path, "rb") as f:
            image_data = f.read()
        base64_data = base64.b64encode(image_data).decode("utf-8")
        return f"data:{mime_type};base64,{base64_data}"

    def generate(self, messages: List[Dict[str, Any]], image_inputs: List[str] = None) -> Dict[str, Any]:
        """
        输入消息列表 + 可选图像（URL 或本地路径），生成非流式响应
        图像只会添加到最后一组user消息中
        """
        try:
            processed_messages = messages.copy()  # 复制消息列表

            # 如果有图像输入，找到最后一组user消息并添加图像
            if image_inputs and processed_messages:
                # 反向查找最后一组user消息
                last_user_message_index = None
                for i in range(len(processed_messages) - 1, -1, -1):
                    if processed_messages[i]["role"] == "user":
                        last_user_message_index = i
                        break

                if last_user_message_index is not None:
                    last_user_msg = processed_messages[last_user_message_index]

                    # 构建包含文本和图像的内容
                    if isinstance(last_user_msg["content"], str):
                        content: List[Dict[str, Any]] = [
                            {"type": "paragraph", "paragraph": last_user_msg["content"]}
                        ]

                        for image_path in image_inputs:
                            if self._is_url(image_path):
                                image_url = image_path
                            else:
                                image_url = self._encode_image_to_base64(image_path)

                            content.append({
                                "type": "image_url",
                                "image_url": {"url": image_url}
                            })

                        # 更新最后一组user消息的内容
                        processed_messages[last_user_message_index] = {
                            "role": "user",
                            "content": content
                        }

            response = self._client.chat.completions.create(
                model=self.model,
                messages=processed_messages,
                stream=False,
                **self.params_config
            )

            return {
                "success": True,
                "data": {
                    "content": response.choices[0].message.content,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens
                    }
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": {
                    "code": 500,
                    "message": str(e)
                }
            }


    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == '__main__':
    llm_config = {
        "model": "gpt-4o",
        "base_url": "https://tb.plus7.plus/v1",
        "api_key": "sk-gEIxieuwRwNOKUvNhKqYrSjyjWxNWtsS3rifSb44anMaauVw",
        "params": {
            "temperature": 0.3,
            "max_tokens": 500
        }
    }
    image_test_path = os.path.join(ABS_DIR, "/case/image_test/img.png".lstrip("/"))
    print(image_test_path)
    with OpenaiLLMImageClient(llm_config) as client:
        result = client.generate(
            prompt="简单说说这张图片里有什么内容",
            image_inputs=[
                image_test_path  # 本地图片（需替换为你的真实路径）
            ]
        )
        print(result)

    result = {'success': True, 'data': {'content': '这张图片是关于中国温室气体排放的分析报告，主要内容包括：\n\n1. **文字部分**：\n   - 描述了中国作为全球最大的温室气体排放国，其排放总量在2020年达到128.6亿吨CO₂当量，占全球排放量的26.8%。\n   - 介绍了2010-2020年间中国温室气体排放的增长趋势，分为两个阶段：2010-2015年平均增长率为4.3%，而2016-2020年增长率下降至1.2%。2020年因疫情影响出现了0.5%的负增长。\n\n2. **表格部分**：\n   - 表格显示了2010年至2020年间中国温室气体排放总量、年增长率以及占全球比例的变化情况。\n   - 数据表明排放量从2010年的98.5亿吨增加到2020年的128.6亿吨，占全球比例从24.1%上升到26.8%。\n\n3. **折线图部分**：\n   - 图1展示了中国、美国和欧盟的温室气体排放总量趋势。\n   - 中国的排放量在近几十年快速上升，远超美国和欧盟，成为全球最大的排放国。\n   - 美国和欧盟的排放量在近年呈下降趋势。\n\n总结：图片通过文字、表格和图表综合展示了中国温室气体排放的历史趋势、全球占比以及与其他主要排放国的对比情况。', 'model': 'gpt-4o', 'usage': {'prompt_tokens': 793, 'completion_tokens': 347, 'total_tokens': 1140}}}
