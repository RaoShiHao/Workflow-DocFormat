import os,copy,re
from win32com.client import constants
from tools.modify.tool_config import ContextToolsConfig
class ImageReader():
    def __init__(self, pyconfig=ContextToolsConfig(config_path="config/reader/image_reader_config.yaml")):
        self.config = pyconfig.config
    def pt_to_convert(self, value, unit):
        value = float(value)
        # 加速运算，直接读取换算值
        # execl = win32com.client.Dispatch("Excel.Application")
        # cm_unit = execl.CentimetersToPoints(1)
        # inches_unit = execl.InchesToPoints(1)
        cm_unit = 28.346456692913385
        inches_unit = 72.0
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit == "pt" or unit == "point":
            return value
        elif unit == "cm":
            return round(value/cm_unit,2)
        elif unit == "mm":
            return round(10*value/cm_unit,2)
        elif unit == "inches":
            return round(value/inches_unit,2)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def get_image(self, doc, image_index):
        """
        获取 Word 文档中的嵌入式图片（InlineShape）
        :param doc: Word 文档对象
        :param image_index: 图片索引（从 1 开始）
        :return: InlineShape 对象 或 错误信息
        """
        try:
            total = doc.InlineShapes.Count
            if total == 0:
                raise ValueError("文档中没有嵌入式图片。")
            if not (1 <= image_index <= total):
                raise IndexError(f"图片索引 {image_index} 超出范围（1 - {total}）。")
            image = doc.InlineShapes(image_index)
            return image
        except Exception as e:
            return None

    def pt_to_percent(self,value, PageSetup):
        page_width = PageSetup.PageWidth - PageSetup.LeftMargin - PageSetup.RightMargin
        percent = round(value / page_width * 100,2)
        return percent

    def __get_size(self, image,*args,**kwargs):
        width_pt = image.Width
        height_pt = image.Height
        lock_aspect_ratio = image.LockAspectRatio
        return {"width": width_pt, "height": height_pt, "lock_aspect_ratio": lock_aspect_ratio, }

    def __get_alignment(self, image,*args,**kwargs):
        # 获取图片所在段落
        paragraph = image.Range.Paragraphs(1)
        alignment_value = paragraph.Alignment
        # 反向映射为字符串
        mapping = {
            0: "left",
            1: "center",
            2: "right"
        }
        alignment = mapping.get(alignment_value, "unknown")
        return {
            "alignment": alignment
        }

    def __get_pagination(self, image,*args,**kwargs):
        paragraph = image.Range.ParagraphFormat
        return {
            "keep_with_next": paragraph.KeepWithNext,
            "keep_together": paragraph.KeepTogether,
            "page_break_before": paragraph.PageBreakBefore
        }

    def __get_image_format(self, image, key_list, *args,**kwargs):
        try:
            property_get_dict = {
                "size": self.__get_size,
                "pagination": self.__get_pagination,
                "alignment": self.__get_alignment
             }
            format_dict = {}
            for key in key_list:
                if key in property_get_dict:
                    format_dict[key] = property_get_dict.get(key)(image)
            result = {
                "status":"success",
                "properties":format_dict
            }
        except Exception as e:
            print(f"Image format get Error! The detail is {e}")
            result = {
                "status": "error",
                "exception": e
            }
        finally:
            return result

    def get_image_format(self, doc, image_index, key_list = ["size", "pagination", "alignment"], *args,**kwargs):
        image = self.get_image(doc,image_index)
        return self.__get_image_format(image,key_list)

    def get_images_format(self,doc):
        image_num = doc.InlineShapes.Count
        formats = []
        for index in range(image_num):
            image_index = index+1
            format = self.get_image_format(doc,image_index)
            if format.get("status") == "success":
                formats.append({"style_name":image_index,"image_list":[image_index],"format_properties":format.get("properties")})
        return formats
    def get_images_info(self, doc, before=0, after=0, *args,**kwargs):
        try:
            image_info_result = []
            for image_index in range(1, doc.InlineShapes.Count + 1):
                image_image_result = self.__get_image_info(doc, image_index=image_index, before=before, after=after)
                if image_image_result.get("state") == "success":
                    image_image_result.pop("state")
                    image_info_result.append(image_image_result)
            return image_info_result
        except Exception as e:
            print(f"Get image info error! The detail is: {e}")
            raise

    def __get_image_info(self, doc, image_index, before=0, after=0, *args, **kwargs):
        """
        获取指定图片的详细信息（包括所在页码、前后文本等）
        """
        try:
            all_paragraphs = list(doc.Paragraphs)

            # --- 1️⃣ 获取图片对象 ---
            image = self.get_image(doc, image_index)
            img_range = image.Range

            # --- 2️⃣ 页码信息 ---
            start_page = img_range.Information(constants.wdActiveEndPageNumber)

            # --- 3️⃣ 段落索引确定 ---
            img_start = img_range.Start
            img_para_index = None

            for i, para in enumerate(all_paragraphs, start=1):
                if para.Range.Start <= img_start <= para.Range.End:
                    img_para_index = i
                    break

            # --- 4️⃣ 获取前后段落（改进版）---
            before_paras = []
            after_paras = []
            current_para_text = ""

            if img_para_index:
                # 获取当前段落文本（排除图片本身）
                current_para = all_paragraphs[img_para_index - 1]
                current_para_text = self.__get_paragraph_text_without_image(current_para, img_range)

                # 向前获取段落（图片之前的段落）
                start_before_idx = max(0, img_para_index - 1 - before)
                before_count = 0
                i = img_para_index - 2  # 从当前段落的前一个开始

                while i >= start_before_idx and before_count < before:
                    if i >= 0:  # 确保索引有效
                        para_text = all_paragraphs[i].Range.Text
                        cleaned_text = self.__clean_paragraph_text(para_text)
                        if cleaned_text:
                            before_paras.insert(0, cleaned_text)  # 按顺序插入
                            before_count += 1
                    i -= 1

                # 向后获取段落（图片之后的段落）- 如果遇到过滤段落则继续查找
                after_count = 0
                i = img_para_index  # 从当前段落的下一个开始

                while i < len(all_paragraphs) and after_count < after:
                    para_text = all_paragraphs[i].Range.Text
                    cleaned_text = self.__clean_paragraph_text(para_text)

                    if cleaned_text:
                        after_paras.append(cleaned_text)
                        after_count += 1
                    # 如果这个段落被过滤掉了，继续检查下一个，但不增加计数
                    # 这样可以确保我们获取到指定数量的有效段落

                    i += 1

                # 调试信息
                # print(f"调试信息 - 图片所在段落索引: {img_para_index}")
                # print(f"调试信息 - 向前获取了 {len(before_paras)} 个有效段落")
                # print(f"调试信息 - 向后获取了 {len(after_paras)} 个有效段落")
                # print(f"调试信息 - 向后段落内容: {after_paras}")

            # --- 5️⃣ 图片基本属性 ---
            image_size = self.__get_size(image)
            alignment = self.__get_alignment(image)

            # --- 6️⃣ 汇总结果 ---
            return {
                "state": "success",
                "image_index": image_index,
                "page_number": start_page,
                "current_paragraph": current_para_text,
                "before_paragraphs": before_paras,
                "after_paragraphs": after_paras,
                "size": image_size,
                "alignment": alignment
            }
        except Exception as e:
            print(f"Get image information error! The detail is {e}")
            return {
                "state": "error",
                "message": str(e)
            }

    def __clean_paragraph_text(self, text):
        """
        清理段落文本，移除特殊字符和空白段落
        """
        if not text:
            return ""

        # 移除换行符、制表符等特殊字符
        cleaned = text.replace('\r', '').replace('\x07', '').replace('\x0b', '').strip()

        # 过滤掉只包含特殊字符的段落
        special_chars = {'/', '|', '-', '*', ' ', '\t', '\n', '\r', '\x0c', '\x00'}
        if all(c in special_chars or c.isspace() for c in cleaned):
            return ""

        # 过滤掉过短的段落（可能只是分隔符）
        if len(cleaned) <= 1 and cleaned in special_chars:
            return ""

        return cleaned

    def __get_paragraph_text_without_image(self, paragraph, image_range):
        """
        获取段落文本，但排除指定图片范围的文本
        """
        try:
            para_range = paragraph.Range
            para_text = para_range.Text

            # 如果图片范围在段落范围内，则从段落文本中移除图片对应的部分
            if (image_range.Start >= para_range.Start and
                    image_range.End <= para_range.End):
                # 计算图片在段落文本中的位置
                start_pos = image_range.Start - para_range.Start
                end_pos = image_range.End - para_range.Start

                # 移除图片对应的文本部分
                para_text = para_text[:start_pos] + para_text[end_pos:]

            return self.__clean_paragraph_text(para_text)
        except Exception as e:
            print(f"Error extracting paragraph text without image: {e}")
            return self.__clean_paragraph_text(paragraph.Range.Text)

    def __read_size(self, image, doc, image_info, *args, **kwargs):
        width_pt = image.Width
        height_pt = image.Height
        lock_aspect_ratio = image.LockAspectRatio

        image_info["size"]['width']['value']["pt"] = self.pt_to_convert(width_pt, "pt")
        image_info["size"]['width']['value']["cm"] = self.pt_to_convert(width_pt, "cm")
        image_info["size"]['width']['value']["mm"] = self.pt_to_convert(width_pt, "mm")
        image_info["size"]['width']['value']["inches"] = self.pt_to_convert(width_pt, "inches")
        image_info["size"]['width']['value']["percent"] = self.pt_to_percent(width_pt, doc.PageSetup)

        image_info["size"]['height']['value']["pt"] = self.pt_to_convert(height_pt, "pt")
        image_info["size"]['height']['value']["cm"] = self.pt_to_convert(height_pt, "cm")
        image_info["size"]['height']['value']["mm"] = self.pt_to_convert(height_pt, "mm")
        image_info["size"]['height']['value']["inches"] = self.pt_to_convert(height_pt, "inches")

        image_info["size"]["lock_aspect_ratio"]['value'] = lock_aspect_ratio

        return image_info

    def __read_alignment(self, image, image_info,*args,**kwargs):
        # 获取图片所在段落
        paragraph = image.Range.Paragraphs(1)
        alignment_value = paragraph.Alignment
        # 反向映射为字符串
        mapping = {
            0: "left",
            1: "center",
            2: "right"
        }
        alignment = mapping.get(alignment_value, "unknown")

        image_info["alignment"]["value"] = alignment
        return image_info

    def __read_pagination(self, image, image_info,*args,**kwargs):
        paragraph = image.Range.ParagraphFormat
        image_info['pagination']["keep_with_next"]["value"] = paragraph.KeepWithNext
        image_info['pagination']["keep_together"]["value"] = paragraph.KeepTogether
        image_info['pagination']["page_break_before"]["value"] = paragraph.PageBreakBefore
        return image_info

    def read_image_properties(self, doc, image_index, params_list=[], language='zh',*args,**kwargs):
        # print(function_list)
        # print(params_list)
        attribution_dict = {
            "size": self.__read_size,
            "pagination": self.__read_pagination,
            "alignment": self.__read_alignment,
        }
        # 加载读取模板
        template = self.config.get("properties_template")
        if language in ['zh', 'en']:
            image_info = copy.deepcopy(template.get(language))
        else:
            image_info = copy.deepcopy(template.get("zh"))
            print("Default Using Chinese")
        try:
            # 字符串转换
            image_index = int(image_index)
            if image_index > 0:
                image = self.get_image(doc,image_index)
            else:
                print("image index must >= 0!")
                raise
            if not params_list:
                # 未指定读取属性范围，默认全都要读取
                params_list = list(attribution_dict.keys())
            else:
                # 指定读取属性范围以后，删除模板中不需要读取的键值对
                for attribution in attribution_dict.keys():
                    if attribution not in params_list:
                        image_info.pop(attribution)

            # 依次获取要读取的属性
            for params in params_list:
                # 调用参数被支持
                if params in attribution_dict:
                    attribution_info_read_tool = attribution_dict.get(params)
                    image_info = attribution_info_read_tool(image=image, image_info=image_info,doc=doc)
                    # print(image_info)
                    # print(params)

            # 返回成功结果
            return {"state": "success", "properties": image_info}

        except Exception as e:
            # 捕获异常并返回错误信息

            return {"state": "false", "image_index": image_index, "exception": str(e)}


if __name__ == '__main__':
    from constant import ABS_DIR
    import os
    import win32com.client as win32
    word = win32.DispatchEx("Word.Application")
    word.Visible = True  # 设为可见（调试时建议开启）
    word_file_path = "file/image_base.docx"

    word_file_path = os.path.join(ABS_DIR, word_file_path)
    # 打开已有文档
    try:
        # 打开文档
        doc = word.Documents.Open(word_file_path)
        reader_tool = ImageReader()

        print(reader_tool.get_images_format(doc))

    except Exception as e:
        print(f"操作失败：{str(e)}")
        raise

    finally:
        # 确保清理资源
        if 'doc' in locals():
            doc.Close(SaveChanges=False)
        word.Quit()

