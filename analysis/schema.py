from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional,Union
from abc import ABC, abstractmethod
class FormatState(BaseModel):
    # 基础输入
    template_doc_path: str = Field(default="", description="模板文档路径")
    source_doc_path: str = Field(default="", description="待迁移格式的文档路径")

    # Step 1: 模板解析结果
    template_content_info: List[Dict[str, Any]] = Field(default_factory=list, description="包含模板文档的content信息")
    template_format_styles: List[Dict[str, Any]] = Field(default_factory=list, description="模板文档整体的格式样式信息")
    template_analyses: List[Dict[str, Any]] = Field(default_factory=list, description="每个样式的分析结果")
    format_labels: List[Dict[str, Any]] = Field(default_factory=list, description="样式标签列表，包含每个样式的说明")
    n_styles: int = Field(default=0, description="读取到的styles数量")

    # Step 2: 内容-格式对齐
    source_content_info: List[Dict[str, Any]] = Field(default_factory=list, description="源文档的content信息")
    style_alignment_map: Dict[str, Any] = Field(default_factory=dict, description="模板与源文档的style对齐映射")

    # 错误与状态信息
    error: Optional[str] = Field(default=None)



class BaseFormatAnalyzer(ABC):
    """
    格式分析任务的抽象基类。
    定义从模板解析到格式迁移的通用接口。
    """

    # ---------- Step 1: 模板解析 ----------
    @abstractmethod
    def extract_content(self, doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        提取文档的内容结构信息。
        例如：段落、表格、图片等。
        """
        raise NotImplementedError

    @abstractmethod
    def extract_styles(self, doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        提取文档中定义的样式信息。
        返回值通常为样式对象列表或样式名与格式的映射。
        """
        raise NotImplementedError

    @abstractmethod
    def analyze_template(self, styles: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """
        对模板的样式进行分析与总结。
        可能包括对样式语义、层级、用途的识别。
        """
        raise NotImplementedError

    # ---------- Step 2: 样式匹配 ----------
    @abstractmethod
    def match_styles(self, styles: List[Dict[str, Any]], target_doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        将模板样式与源文档内容进行匹配。
        返回匹配后的结构化结果，如列表或映射。
        """
        raise NotImplementedError

    @abstractmethod
    def classify_styles(self, style_list: List[Dict[str, Any]], style_dict: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        对样式进行分类或标签化。
        通常在 match_styles 过程中调用，用于对齐样式语义。
        """
        raise NotImplementedError

    # ---------- Step 3: 样式应用 ----------
    @abstractmethod
    def apply_styles(
        self,
        doc_path: str,
        label_list: List[Dict[str, Any]],
        style_list: List[Dict[str, Any]],
        **kwargs
    ) -> Optional[str]:
        """
        将样式应用到目标文档。
        返回值可以是保存后的文件路径或 None。
        """
        raise NotImplementedError


class FormatWorkflowBase:
    """通用格式迁移工作流基类"""
    def __init__(self):
        self.graph = None

    def compile(self):
        """编译生成LangGraph图"""
        raise NotImplementedError("子类必须实现 compile()")

    def run(self, state: FormatState):
        """运行图"""
        if self.graph is None:
            raise ValueError("Graph 尚未编译。请先调用 compile()。")
        return self.graph.compile().invoke(state)

