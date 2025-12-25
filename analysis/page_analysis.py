from langgraph.graph import StateGraph, END, START
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import yaml,os
import win32com.client as win32
from tools.reader.page_reader import PageReader
from tools.modify.page_tool import PageTools
from schema import FormatState, BaseFormatAnalyzer, FormatWorkflowBase
from constant import ABS_DIR
class PageFormatAnalyzer(BaseFormatAnalyzer):
    def __init__(self, config_path="config/page_analysis.yaml"):
        abs_path = os.path.join(ABS_DIR,config_path)
        with open(abs_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.content_x = self.config.get("content_x", 1)
        self.reader = PageReader()
        self.modify_tool = PageTools()
        self.prompt = self.config.get("prompt")
        self.language = self.config.get("language","zh")

    def extract_content(self, doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        提取文档的内容结构信息。
        例如：段落、表格、图片等。
        """
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设置为True以便查看操作过程
            doc = word.Documents.Open(os.path.join(ABS_DIR,doc_path))
            section_infos = self.reader.get_page_info(doc,content_x=self.content_x)
        except Exception as e:
            print(f"Get Section information Error! The detail is:{e}")
            section_infos = None
        finally:
            # 确保清理资源
            if 'doc' in locals():
                doc.Close(SaveChanges=False)
            word.Quit()
            return section_infos

    def extract_styles(self, doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        提取文档中定义的样式信息。
        返回值通常为样式对象列表或样式名与格式的映射。
        """
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设置为True以便查看操作过程
            doc = word.Documents.Open(os.path.join(ABS_DIR,doc_path))
            styles_list = self.reader.get_page_styles(doc)
        except Exception as e:
            print(f"Get Section information Error! The detail is:{e}")
            styles_list = None
        finally:
            # 确保清理资源
            if 'doc' in locals():
                doc.Close(SaveChanges=False)
            word.Quit()
            return styles_list

    def section_summary(self, section_info):
        pass

    def analyze_template(self, doc_path: str, **kwargs):
        """
        对模板的样式进行分析与总结。
        可能包括对样式语义、层级、用途的识别。
        """
        labels_list = []
        styles_list = self.extract_styles(doc_path)
        n_styles = len(styles_list)
        if n_styles > 1:
            section_info_list = self.extract_content(doc_path)
            print(section_info_list)
        else:
            labels_list = [{"style_name":"only_one","format_properties":styles_list[0]}]
        return labels_list
    # ---------- Step 2: 样式匹配 ----------
    def match_styles(self, labels_list: List[Dict[str, Any]], target_doc_path: str, **kwargs) -> List[Dict[str, Any]]:
        """
        将模板样式与源文档内容进行匹配。
        返回匹配后的结构化结果，如列表或映射。
        """
        classify_results = []
        n_labels = len(labels_list)
        if n_labels > 1:
            pass
        else:
            classify_results = [{"style_name":"only_one", "section_list":["all"]}]
        return classify_results

    def classify_styles(self, style_list: List[Dict[str, Any]], style_dict: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        对样式进行分类或标签化。
        通常在 match_styles 过程中调用，用于对齐样式语义。
        """
        pass

    def __label_list_convert(self, label_list):
        label_dict = {}
        for label in label_list:
            key = label.get("style_name")
            value = label.get("format_properties")
            label_dict[key] = value
        return label_dict


    # ---------- Step 3: 样式应用 ----------
    def apply_styles(self,doc_path: str, label_list: List[Dict[str, Any]], classify_list: List[Dict[str, Any]],**kwargs):
        """
        将样式应用到目标文档。
        返回值可以是保存后的文件路径或 None。
        """
        try:
            word = win32.DispatchEx("Word.Application")
            word.Visible = True  # 设置为True以便查看操作过程
            doc = word.Documents.Open(doc_path)

            label_dict = self.__label_list_convert(label_list)
            for classify in classify_list:
                style_name = classify.get("style_name")
                section_list = classify.get("section_list")
                format_properties = label_dict.get(style_name)
                self.modify_tool.set_format(doc,section_list,settings=format_properties)
            doc.Save()
        except Exception as e:
            print(f"Set Section page format Error! The detail is:{e}")
        finally:
            # 确保清理资源
            if 'doc' in locals():
                doc.Close(SaveChanges=False)
            word.Quit()



# ========== 3️⃣ PageFormat 工作流类 ========== 
class PageFormat(FormatWorkflowBase):
    def __init__(self):
        super().__init__()
        self.analyzer = PageFormatAnalyzer()

    # ---------- 节点函数 ----------
    def analyze_template(self, state: FormatState) -> FormatState:
        """Step 1: 分析模板文档"""
        print("[analyze_template] analyzing template:", state.template_doc_path)
        state.template_content_info = [{"id": 1, "paragraph": "标题", "type": "heading"}]
        state.template_format_styles = self.analyzer.analyze_template(state.template_doc_path)
        state.template_analyses = [{"paragraph": "heading", "usage": "文档标题"}]
        state.format_labels = [{"label": "Title", "desc": "主标题样式"}]
        state.n_styles = len(state.template_format_styles)
        return state

    def match_styles(self, state: FormatState) -> FormatState:
        """Step 2: 匹配源文档内容"""
        print("[match_styles] matching source content:", state.source_doc_path)
        state.source_content_info = [{"id": 1, "paragraph": "源文档标题", "type": "heading"}]
        return state

    def classify_styles(self, state: FormatState) -> FormatState:
        """Step 3: 样式分类与对齐"""
        print("[classify_styles] generating paragraph alignment map...")
        state.style_alignment_map = {"heading": "Title"}
        return state

    def apply_styles(self, state: FormatState) -> FormatState:
        """Step 4: 应用样式迁移"""
        print("[apply_styles] applying styles to:", state.source_doc_path)
        print("使用对齐映射:", state.style_alignment_map)
        # 模拟修改文档
        print("已将源文档标题样式更新为模板样式。")
        return state


    # ---------- 构建 LangGraph ----------
    def compile(self):
        graph = StateGraph(FormatState)

        # 注册节点
        graph.add_node("analyze_template", self.analyze_template)
        graph.add_node("match_styles", self.match_styles)
        graph.add_node("classify_styles", self.classify_styles)
        graph.add_node("apply_styles", self.apply_styles)

        # 设置起点
        graph.set_entry_point("analyze_template")

        # 设置节点连接顺序
        graph.add_edge("analyze_template", "match_styles")
        graph.add_edge("match_styles", "classify_styles")
        graph.add_edge("classify_styles", "apply_styles")
        graph.add_edge("apply_styles", END)

        self.graph = graph
        print("✅ LangGraph 编译完成！")
        return graph


# ========== 4️⃣ 示例运行 ==========
if __name__ == "__main__":
    initial_state = FormatState(
        template_doc_path="file/template.docx",
        source_doc_path="file/template.docx"
    )

    analyzer = PageFormat()
    analyzer.compile()

    final_state = analyzer.run(initial_state)
    print("流程执行完毕 ✅")
    print("最终状态：", final_state)






