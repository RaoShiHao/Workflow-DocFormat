import win32com.client as win32
from tools.modify.tool_config import ContextToolsConfig

class ImageBaseTools():
    def __init__(self):
        # 在初始化时创建一次，重复使用
        self.excel_app = win32.Dispatch("Excel.Application")

    def convert_to_pt(self, value, unit):
        execl = self.excel_app
        """将不同单位的间距转换为磅（pt）"""
        if value is None:
            return 0
        if unit in ["pt","point","磅"]:
            return float(value)
        elif unit in ["cm","centimeter","厘米"]:
            return execl.CentimetersToPoints(value)
        elif unit in ["mm","millimeter","毫米"]:
            return execl.CentimetersToPoints(value*0.1)
        elif unit in ["inches","英寸"]:
            return execl.InchesToPoints(value)
        else:
            raise ValueError(f"不支持的单位: {unit}")

    def set_size(self, image, width: float = None, height: float=None,unit: str = "pt", lock_aspect_ratio: int = -1,
                          doc=None):
        """
        设置图片大小（按宽度调整，可选锁定长宽比）

        参数：
            figure: Word 图片对象 (Shape)
            width (float): 图片宽度
            height (float)：图片高度
            unit (str): 单位，可选：
                        "pt" - 磅
                        "cm" - 厘米
                        "mm" - 毫米
                        "inches" - 英寸
                        "percent" - 百分比（相对于页面可用宽度）
            lock_aspect_ratio (bool): 是否锁定长宽比
            doc: Word文档对象（仅在使用percent时必需）
        返回：
            {
                "picture_size": {
                    "status": "success" / "error",
                    "message": "设置结果描述"
                }
            }
        """
        try:
            # ===== 设置是否锁定长宽比 =====
            image.LockAspectRatio = lock_aspect_ratio
            # ===== 检查输入 =====
            if width is None:
                return {
                        "status": "error",
                        "message": "未提供宽度参数 width"
                    }

            # ===== 百分比模式 =====
            if unit.lower() == "percent":
                if doc is None:
                    raise ValueError("使用 percent 单位时，必须提供 doc 参数")

                # 计算页面可用宽度（去除左右页边距）
                page_width = doc.PageSetup.PageWidth - doc.PageSetup.LeftMargin - doc.PageSetup.RightMargin
                target_width = page_width * (width / 100.0)
                image.Width = target_width
                msg = f"Set picture width to {width}% of page width (≈{round(target_width, 2)}pt)"

            # ===== 其他单位（使用统一转换）=====
            else:
                width_pt = self.convert_to_pt(value=width, unit=unit)
                image.Width = width_pt
                if lock_aspect_ratio == -1:
                    msg = f"Set picture width to {width}{unit} ({round(width_pt, 2)}pt)"
                else:
                    if height:
                        height_pt = self.convert_to_pt(value=height,unit=unit)
                        image.Height = height_pt
                        msg = f"Set picture width to {width}{unit} ({round(width_pt, 2)}pt), height to {height}{unit} ({round(height_pt, 2)}pt)"
            return {
                    "status": "success",
                    "message": f"{msg}, lock_aspect_ratio={lock_aspect_ratio}"
            }

        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }

    def set_alignment(self,image,alignment:str):
        try:
            # 获取图片所在段落
            paragraph = image.Range.Paragraphs(1)
            # 设置对齐方式
            alignment = alignment.lower()
            if alignment in ["left","左对齐"]:
                paragraph.Alignment = 0  # wdAlignParagraphLeft
            elif alignment in ["center","居中对齐"]:
                paragraph.Alignment = 1  # wdAlignParagraphCenter
            elif alignment in ["right","右对齐"]:
                paragraph.Alignment = 2  # wdAlignParagraphRight
            else:
                raise ValueError(f"No supported alignment type: {alignment}, only: left, center, right")

            return {"state": "success", "message": f"Set the image alignment to be {alignment}"}
        except Exception as e:
            return {"state": "error", "message": str(e)}

    def set_keep_with_next(self,image, keep_with_next=None):
        try:
            paragraph = image.Range.Paragraphs(1)
            if keep_with_next is not None:
                paragraph.KeepWithNext = -1 if keep_with_next else 0
            return {"status":"success","message":f"Set image keep_with_next = {keep_with_next}"}
        except Exception as e:
            return {
                    "status": "error",
                    "message": str(e)
            }
    def set_keep_together(self, image, keep_together=None):
        try:
            paragraph = image.Range.Paragraphs(1)
            if keep_together is not None:
                paragraph.KeepTogether = -1 if keep_together else 0
            return {"status": "success", "message": f"Set image keep_together = {keep_together}"}
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    def set_page_break_before(self, image, page_break_before=None):
        try:
            paragraph = image.Range.Paragraphs(1)
            if page_break_before is not None:
                paragraph.PageBreakBefore = -1 if page_break_before else 0
            return {"status": "success", "message": f"Set image page_break_before = {page_break_before}"}
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    def set_pagination(self, image, keep_with_next=None, keep_together=None,
                             page_break_before=None):
        try:
            result = {}
            if keep_with_next is not None:
                result["keep_with_next"] = self.set_keep_with_next(image,keep_with_next)
            if keep_together is not None:
                result["keep_together"] = self.set_keep_together(image,keep_together)
            if page_break_before is not None:
                result["page_break_before"] = self.set_page_break_before(image,page_break_before)
            return result
        except Exception as e:
            return {"state": "false", "message": str(e)}
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


class ImageTools():
    def __init__(self):
        self.image_tool = ImageBaseTools()

    def __set_size(self, doc, image_index, width: float = None, height:float=None,unit: str = "pt", lock_aspect_ratio: int = -1):
        image = self.image_tool.get_image(doc,image_index)
        status = self.image_tool.set_size(image=image,width=width,height=height,unit=unit,lock_aspect_ratio=lock_aspect_ratio,doc=doc)
        return {"image_size":status}

    def __set_alignment(self,doc, image_index, alignment):
        image = self.image_tool.get_image(doc, image_index)
        status = self.image_tool.set_alignment(image, alignment)
        return {"image_alignment": status}

    def __set_pagination(self,doc, image_index, keep_with_next=True, keep_together=True,
                             page_break_before=False):
        image = self.image_tool.get_image(doc,image_index)
        status = self.image_tool.set_pagination(image,keep_with_next=keep_with_next,keep_together=keep_together,page_break_before=page_break_before)
        return status

    def set_size(self, doc, image_list, width=None, height=None, unit="pt", lock_aspect_ratio=-1,*args,**kwargs):
        results = {}
        if 'all' in image_list:
            image_list = [i + 1 for i in range(doc.InlineShapes.Count)]
        try:
            for image_index in image_list:
                status = self.__set_size(doc=doc, image_index=image_index, width=width, height=height,
                                                 unit=unit, lock_aspect_ratio=lock_aspect_ratio)
                results = status
            doc.Save()
        except Exception as e:
            results["image_size"] = {
                "status": "error",
                "message": f"Failed to set image size, the detail is : {e}"}
        finally:
            return results

    def set_alignment(self, doc, image_list, alignment="center",*args, **kwargs):
        results = {}
        if 'all' in image_list:
            image_list = [i + 1 for i in range(doc.InlineShapes.Count)]
        try:
            for image_index in image_list:
                status = self.__set_alignment(doc=doc, image_index=image_index, alignment=alignment)
                results = status
            doc.Save()
        except Exception as e:
            results["image_alignment"] = {
                "status": "error",
                "message": f"Failed to set image alignment, the detail is : {e}"}
        finally:
            return results

    def set_pagination(self, doc, image_list, keep_with_next=0, keep_together=0,
                             page_break_before=0,*args, **kwargs):
        results = {}
        if 'all' in image_list:
            image_list = [i + 1 for i in range(doc.InlineShapes.Count)]
        try:
            for image_index in image_list:
                status = self.__set_pagination(doc=doc, image_index=image_index, keep_together=keep_together,keep_with_next=keep_with_next,page_break_before=page_break_before)
                results = status
            doc.Save()
        except Exception as e:
            results["image_pagination"] = {
                "status": "error",
                "message": f"Failed to set image pagination, the detail is : {e}"}
        finally:
            return results


    def set_format(self, doc, location_list, settings={}):
        support_properties = ["size", "alignment", "pagination"]
        support_functions = {
            "size": self.set_size,
            "alignment": self.set_alignment,
            "pagination": self.set_pagination
        }
        for support_property in support_properties:
            if support_property in settings:
                property_setting = settings.get(support_property)
                property_function = support_functions.get(support_property)
                property_function(doc,location_list,**property_setting)
