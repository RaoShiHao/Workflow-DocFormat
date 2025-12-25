import win32com.client
from pdf2image import convert_from_path
import pypandoc
from pathlib import Path
import os, json
import re,shutil
import ast
class FileConverter:
    def __init__(self, base_dir=None):
        """
        初始化转换器，可指定基础目录（用于相对路径转换）
        :param base_dir: 项目根目录，默认为None（使用当前工作目录）
        """
        self.ABS_DIR = os.path.abspath(base_dir) if base_dir else os.getcwd()

    def resolve_mixed_path(self, input_path, base_dir=None):
        """
        处理混合绝对路径+相对路径符号的路径
        :param input_path: 可能包含`./`或`../`的路径（如`D:\project\./images`）
        :param base_dir: 基础目录（默认为当前工作目录）
        :return: 标准化的绝对路径
        """
        # 转换为Path对象并展开相对路径符号
        path = Path(input_path.replace('/', os.sep))  # 统一替换为系统分隔符
        if not path.is_absolute() and base_dir:
            base = Path(base_dir)
            return str((base / path).resolve())
        # 处理绝对路径中的相对符号
        return str(path.resolve())

    def _resolve_path(self, *path_parts):
        combined = os.path.join(*path_parts)
        # 处理混合路径情况
        if any(sym in combined for sym in ('./', '../')):
            return self.resolve_mixed_path(combined, self.ABS_DIR)
        # 常规路径处理
        abs_path = os.path.join(self.ABS_DIR, combined)
        return os.path.abspath(abs_path)


    def md_to_docx(self, input_md, output_docx=None, image_dir=None):
        """
        Markdown转Word文档
        :param input_md: 输入的.md文件路径（相对/绝对）
        :param output_docx: 输出的.docx路径（默认同输入文件名）
        :param image_dir: 图片目录（相对/绝对）
        """
        try:
            # 处理路径
            abs_input = self._resolve_path(input_md)
            abs_output = os.path.abspath(output_docx) if output_docx else \
                os.path.join(self.ABS_DIR, os.path.splitext(os.path.basename(input_md))[0] + ".docx")
            abs_image_dir = self._resolve_path(image_dir) if image_dir else os.path.dirname(abs_input)
            # print(abs_input)
            # print(abs_image_dir)
            # 转换文档
            pypandoc.convert_file(
                abs_input,
                "docx",
                outputfile=abs_output,
                format="markdown",
                extra_args=[
                    f"--resource-path={abs_image_dir}",
                    "--extract-media=images"
                ]
            )
            print(f"MD转DOCX成功: {abs_output}")
            return abs_output
        except Exception as e:
            print(f"MD转DOCX失败: {e}")
            return None

    def docx_to_pdf(self, input_docx, output_pdf=None):
        """
        Word转PDF文档
        :param input_docx: 输入的.docx文件路径（相对/绝对）
        :param output_pdf: 输出的.pdf路径（默认同输入文件名）
        """
        try:
            abs_input = self._resolve_path(input_docx)
            abs_output = os.path.join(output_pdf, os.path.splitext(os.path.basename(input_docx))[0] + ".pdf") if output_pdf else \
                os.path.join(self.ABS_DIR, os.path.splitext(os.path.basename(input_docx))[0] + ".pdf")
            # 使用Word COM接口转换
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(abs_input)
            doc.SaveAs(abs_output, FileFormat=17)  # 17=PDF格式
            doc.Close()
            word.Quit()
            print(f"DOCX转PDF成功: {abs_output}")
            return abs_output
        except Exception as e:
            print(f"DOCX转PDF失败: {e}")
            raise

    def pdf_to_images(self, input_pdf, output_folder=None, dpi=300):
        """
        PDF转图片集
        :param input_pdf: 输入的.pdf文件路径（相对/绝对）
        :param output_folder: 输出目录（默认创建与PDF同名的文件夹）
        :param dpi: 图片分辨率（默认300）
        """
        try:
            abs_input = self._resolve_path(input_pdf)
            abs_output = os.path.join(output_folder, "doc_images") if output_folder else \
                os.path.join(self.ABS_DIR, "doc_images")
            os.makedirs(abs_output, exist_ok=True)
            # 转换PDF为图片
            images = convert_from_path(abs_input, dpi=dpi)
            for i, image in enumerate(images):
                image.save(os.path.join(abs_output, f"page_{i + 1}.png"), "PNG")
            print(f"PDF转图片成功，保存至: {abs_output}")
            return abs_output
        except Exception as e:
            print(f"PDF转图片失败: {e}")
            raise

    def read_json_file(self, file_path, encoding='utf-8'):
        """
        安全读取JSON文件（自动处理编码和格式错误）

        参数:
            file_path (str): JSON文件路径
            encoding (str): 文件编码（默认utf-8）

        返回:
            dict/list/None: 成功返回解析后的数据，失败返回None
        """
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"[错误] 文件不存在: {file_path}")
        except json.JSONDecodeError as e:
            print(f"[错误] 无效的JSON格式: {file_path} 原因: {e.msg} (位置: 行{e.lineno}, 列{e.colno})")
        except UnicodeDecodeError:
            print(f"[错误] 编码问题，请尝试其他编码（如gbk）")
        except Exception as e:
            print(f"[错误] 读取失败: {str(e)}")
        return None

    def write_json_file(self, data, file_path, encoding='utf-8', indent=4):
        """
        安全写入JSON文件（自动创建目录和格式化输出）

        参数:
            data (dict/list): 要写入的Python数据结构
            file_path (str): 输出文件路径
            encoding (str): 文件编码（默认utf-8）
            indent (int): 缩进空格数（None表示压缩格式）

        返回:
            bool: 成功返回True，失败返回False
        """
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding=encoding) as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            print(f"{file_path} save successful")
            return True
        except TypeError:
            print("[错误] 数据包含不可序列化的类型")
        except PermissionError:
            print(f"[错误] 无写入权限: {file_path}")
        except Exception as e:
            print(f"[错误] 写入失败: {str(e)}")
        return False

    def create_folder(self, path):
        """
        创建文件夹（支持多级目录创建）

        参数:
            path (str): 要创建的目录路径

        返回:
            bool: 创建成功返回True，失败返回False
        """
        try:
            # 规范化路径（处理正反斜杠混用问题）
            normalized_path = os.path.normpath(path)

            # 使用pathlib创建目录（自动处理多级目录）
            folder = Path(normalized_path)
            folder.mkdir(parents=True, exist_ok=True)

            print(f"文件夹创建成功: {normalized_path}")
            return True
        except Exception as e:
            print(f"文件夹创建失败: {str(e)}")
            return False

    def list_exctract(self, list_result):
        try:
            """从字符串中提取所有被 ```json 和 ``` 包裹的 JSON 片段"""
            pattern = r'```json(.*?)```'
            matches = re.findall(pattern, list_result, re.DOTALL)
            # 去除前后空白字符（如换行符）
            match_result = [match.strip() for match in matches]
            data = json.loads(match_result[0])
            # print(data)
        except Exception as e:
            print(f"Funcall Extract Error! The detail is {e}")
            data = []
        return data

    def prepare_directory(self,dir_path):
        """
        准备目录：如果目录存在则清空内容，不存在则创建

        参数:
            dir_path: 目标目录路径
        """
        # 检查目录是否存在
        if os.path.exists(dir_path):
            # 检查是否是目录
            if os.path.isdir(dir_path):
                # 遍历目录下的所有内容并删除
                for item in os.listdir(dir_path):
                    item_path = os.path.join(dir_path, item)
                    try:
                        # 如果是文件或符号链接，直接删除
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.unlink(item_path)
                        # 如果是目录，递归删除
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                    except Exception as e:
                        print(f"删除 {item_path} 失败: {e}")
        else:
            # 目录不存在，创建目录（包括可能的父目录）
            try:
                os.makedirs(dir_path, exist_ok=True)
                print(f"目录 {dir_path} 创建成功")
            except Exception as e:
                print(f"创建目录 {dir_path} 失败: {e}")


    def response_json_parse(self, json_str):
        """
        解析 JSON 字符串，支持多种格式：
        - 标准的双引号 JSON
        - Python 风格的单引号字面量
        - 包含 Markdown 代码块的情况

        参数:
            json_str (str): JSON 格式的字符串，可能包含 Markdown 代码块包裹

        返回:
            dict: 解析后的字典；解析失败返回 None
        """

        try:
            # 自动去除 Markdown 代码块包裹
            code_block_pattern = r"```(?:json)?\s*(.*?)\s*```"
            match = re.search(code_block_pattern, json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

            # 使用灵活的 JSON 解析函数
            raw_dict = self.__parse_flexible_json(json_str)
            return raw_dict

        except json.JSONDecodeError as e:
            print("❌ JSON 解析错误:")
            print(f"{e}")
            print("\n⚠️ 原始 JSON 字符串:\n", json_str)
            return None
        except Exception as e:
            print("❌ 发生其他错误:")
            print(f"{type(e).__name__}: {e}")
            print("\n⚠️ 原始 JSON 字符串:\n", json_str)
            return None

    def __parse_flexible_json(self, json_str):
        """
        灵活解析 JSON 字符串，支持单引号和双引号
        """
        # 先尝试标准 JSON 解析
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 如果是单引号格式，尝试转换
            if "'" in json_str and '"' not in json_str:
                try:
                    # 使用 ast.literal_eval 解析 Python 字面量
                    return ast.literal_eval(json_str)
                except (SyntaxError, ValueError):
                    # 尝试简单的引号替换
                    try:
                        return json.loads(json_str.replace("'", '"'))
                    except json.JSONDecodeError:
                        pass
            # 如果还有其他情况，可以继续尝试其他方法
            raise