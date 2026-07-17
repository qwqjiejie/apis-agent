"""MinerUReader — MinerU PDF 智能解析引擎。

MinerU 是一款开源的 PDF 解析工具，可将 PDF 转为结构化 Markdown，
保留标题层级、表格、公式等原始排版信息。

MinerU 不可用时自动降级为 pdfplumber。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("apis")


class MinerUReader:
    """MinerU PDF 智能解析引擎。

    使用方式:
        reader = MinerUReader()
        if reader.available:
            result = await reader.parse(pdf_path)
    """

    def __init__(
        self,
        backend: str = "pipeline",
        parse_method: str = "auto",
        lang: str = "ch",
        formula_enable: bool = True,
        table_enable: bool = True,
        timeout_seconds: float = 300.0,
    ):
        self._backend = backend
        self._parse_method = parse_method
        self._lang = lang
        self._formula_enable = formula_enable
        self._table_enable = table_enable
        self._timeout = timeout_seconds
        self._available: bool | None = None  # None = 未检测

    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._check_cli()
        return self._available

    @property
    def available(self) -> bool:
        return self.is_available()

    def _check_cli(self) -> bool:
        """检查 magic-pdf CLI 是否可用。"""
        try:
            result = subprocess.run(
                ["magic-pdf", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            ok = result.returncode == 0
            if ok:
                logger.info(f"[MinerU] magic-pdf 可用: {result.stdout.strip()[:80]}")
            else:
                logger.warning("[MinerU] magic-pdf 未安装，降级 pdfplumber")
            return ok
        except FileNotFoundError:
            logger.warning("[MinerU] magic-pdf 未找到，降级 pdfplumber。安装: pip install magic-pdf")
            return False
        except Exception as e:
            logger.warning(f"[MinerU] 检测失败: {e}")
            return False

    async def parse(self, pdf_path: str | Path, output_dir: str | Path | None = None) -> str | None:
        """解析 PDF 文件，返回 Markdown 文本。

        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录（默认自动生成临时目录）

        Returns:
            解析后的 Markdown 文本，失败返回 None
        """
        if not self.is_available():
            return None

        import tempfile
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            logger.error(f"[MinerU] 文件不存在: {pdf_path}")
            return None

        if output_dir is None:
            output_dir = Path(tempfile.mkdtemp(prefix="mineru_"))
        else:
            output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "magic-pdf", "parse",
            str(pdf_path),
            "--output-dir", str(output_dir),
            "--method", self._parse_method,
            "--lang", self._lang,
        ]
        if not self._formula_enable:
            cmd.append("--no-formula")
        if not self._table_enable:
            cmd.append("--no-table")

        try:
            import asyncio
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout,
            )

            if process.returncode != 0:
                logger.warning(f"[MinerU] 解析失败: {stderr.decode()[:200]}")
                return None

            # 读取输出的 Markdown 文件
            md_files = list(output_dir.glob("*.md"))
            if not md_files:
                # 尝试在子目录中查找
                for sub in output_dir.iterdir():
                    if sub.is_dir():
                        md_files = list(sub.glob("*.md"))
                        if md_files:
                            break

            if md_files:
                content = md_files[0].read_text(encoding="utf-8")
                logger.info(f"[MinerU] 解析成功: {pdf_path.name} → {len(content)} 字符")
                return content

            return None

        except asyncio.TimeoutError:
            logger.warning(f"[MinerU] 解析超时 ({self._timeout}s): {pdf_path.name}")
            return None
        except Exception as e:
            logger.error(f"[MinerU] 异常: {e}")
            return None

    def parse_sync(self, pdf_path: str | Path, output_dir: str | Path | None = None) -> str | None:
        """同步版 parse（用于线程池）。"""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.parse(pdf_path, output_dir))


mineru_reader = MinerUReader()
