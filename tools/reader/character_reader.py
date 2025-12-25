import os,copy,re
from win32com.client import constants
import win32com
from tools.modify.tool_config import ContextToolsConfig

class TextReader():
    def __init__(self, pyconfig=ContextToolsConfig("/config/Tools/reader/text_reader_config.yaml")):
        self.config = pyconfig.config

    def __get_target_ranges_by_index(self, doc, paragraph_index, start, length):
        """
        按字符索引定位Range对象（自动兼容表格/正文）
        """
        try:
            if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                print(f"[Error] Paragraph index {paragraph_index} out of range.")
                return None

            para = doc.Paragraphs(paragraph_index)
            rng = para.Range

            # 判断段落是否在表格中
            wdWithInTable = 12
            if rng.Information(wdWithInTable):
                # 重新取单元格内段落，避免Characters为空
                cell = rng.Cells(1)
                inner_para = cell.Range.Paragraphs(1)
                rng = inner_para.Range

            para_len = rng.Characters.Count
            if start < 1 or start > para_len or start + length - 1 > para_len:
                print(
                    f"[Error] Invalid char range: paragraph has {para_len} chars, requested {start}-{start + length - 1}.")
                return None

            target_range = rng.Characters(start).Duplicate
            target_range.End = rng.Characters(start + length - 1).End
            return target_range

        except Exception as e:
            print(f"[Error] get_target_range_by_index failed: {e}")
            return None


    def __get_target_ranges_by_text(self, doc, paragraph_index, target_text, match_index=1):
        """
        按文本匹配定位Range对象（自动兼容表格/正文）
        match_index 可为 int 或 "all"
        """
        try:
            if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                print(f"[Error] Paragraph index {paragraph_index} out of range.")
                return None

            para = doc.Paragraphs(paragraph_index)
            rng = para.Range

            # 判断是否在表格中
            wdWithInTable = 12
            if rng.Information(wdWithInTable):
                cell = rng.Cells(1)
                inner_para = cell.Range.Paragraphs(1)
                rng = inner_para.Range

            matches = []
            current_start = rng.Start

            while current_start < rng.End:
                search_rng = rng.Duplicate
                search_rng.Start = current_start
                find = search_rng.Find
                find.Text = target_text
                find.Forward = True
                find.MatchCase = False

                if not find.Execute():
                    break

                matches.append(search_rng.Duplicate)
                current_start = search_rng.End

                if isinstance(match_index, int) and len(matches) >= match_index:
                    break

            if not matches:
                print(f"[Info] No match for paragraph '{target_text}' in paragraph {paragraph_index}")
                return None

            if match_index == "all":
                return matches
            elif isinstance(match_index, int) and match_index <= len(matches):
                return matches[match_index - 1]
            else:
                return None

        except Exception as e:
            print(f"[Error] get_target_range_by_text failed: {e}")
            return None


    def __get_target_ranges_by_regex(self, doc, paragraph_index, regex_pattern, match_index=1):
        """
        按正则匹配定位Range对象（自动兼容表格/正文）
        match_index 可为 int 或 "all"
        """
        try:
            if paragraph_index < 1 or paragraph_index > doc.Paragraphs.Count:
                print(f"[Error] Paragraph index {paragraph_index} out of range.")
                return None

            para = doc.Paragraphs(paragraph_index)
            rng = para.Range

            # 判断是否在表格中
            wdWithInTable = 12
            if rng.Information(wdWithInTable):
                cell = rng.Cells(1)
                inner_para = cell.Range.Paragraphs(1)
                rng = inner_para.Range

            text = rng.Text
            matches = list(re.finditer(regex_pattern, text))
            if not matches:
                print(f"[Info] No regex match for '{regex_pattern}' in paragraph {paragraph_index}")
                return None

            # 支持 match_index = int 或 "all"
            result_ranges = []
            for i, m in enumerate(matches):
                start = rng.Start + m.start()
                end = rng.Start + m.end()
                result_ranges.append(doc.Range(Start=start, End=end))

            if match_index == "all":
                return result_ranges
            elif isinstance(match_index, int) and 1 <= match_index <= len(result_ranges):
                return result_ranges[match_index - 1]
            else:
                return None

        except Exception as e:
            print(f"[Error] get_target_range_by_regex failed: {e}")
            return None


    def get_target_ranges(self, doc, paragraph_list, mode, text_params=None):
        """
        统一的range对象获取接口

        参数:
            doc: Word文档对象
            paragraph_list: 段落索引列表或包含'all'的列表
            mode: 定位模式，可选 'index', 'paragraph', 'regex'
            params: 参数字典，根据模式不同包含不同的参数

        返回:
            包含目标range对象和信息的字典
        """
        if text_params is None:
            text_params = {}
        try:
            if mode == 'index':
                return self.__get_target_ranges_by_index(
                    doc, paragraph_list,
                    text_params.get('start', 1),
                    text_params.get('length', 1)
                )
            elif mode == 'paragraph':
                return self.__get_target_ranges_by_text(
                    doc, paragraph_list,
                    text_params.get('paragraph', ''),
                )
            elif mode == 'regex':
                return self.__get_target_ranges_by_regex(
                    doc, paragraph_list,
                    text_params.get('pattern', ''),
                )
            else:
                return {
                    "status": "error",
                    "message": f"Unsupported mode: {mode}",
                    "ranges": [],
                    "target_ranges": []
                }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Error in range selection: {str(e)}",
                "ranges": [],
                "target_ranges": []
            }

