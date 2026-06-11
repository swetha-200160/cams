# utils/ocr_extractor.py
# ──────────────────────────────────────────────────────────────
# EasyOCR wrapper used by DocumentLoader for image files and
# scanned PDF page text extraction.
#
# Changes from previous version:
#   - Removed Streamlit dependency entirely. The pipeline runs
#     independently of any web framework; framework-specific
#     caching belongs at the application layer, not here.
#   - Fixed gpu=True being hardcoded while the comment said
#     "Force CPU usage". Now uses auto-detection by default:
#     GPU is attempted first; CPU is the fallback.
#   - gpu parameter is exposed on __init__ for callers that
#     want to override (e.g. force CPU in a test environment).
#   - extract() raises typed exceptions instead of catching and
#     re-raising bare Exception, so callers can distinguish
#     FileNotFoundError from OCR engine errors.
#   - Confidence threshold promoted to a class constant so it
#     is easy to adjust without hunting through the method body.
# ──────────────────────────────────────────────────────────────

import os
import logging
import easyocr

logger = logging.getLogger(__name__)


class OcrExtractor:
    """
    Thin wrapper around EasyOCR for image and scanned-PDF text extraction.

    Lazy initialisation — the EasyOCR Reader is created only on the
    first call to extract(), not at construction time, so importing
    this module has zero cost when OCR is not used in a run.
    """

    # Regions with confidence below this value are discarded.
    CONFIDENCE_THRESHOLD: float = 0.5

    def __init__(self, languages: list = None, gpu: bool = None) -> None:
        """
        Args:
            languages : EasyOCR language codes. Defaults to ['en'].
            gpu       : GPU preference.
                          None  (default) → auto-detect: try GPU, fall back to CPU.
                          True            → force GPU (raises if unavailable).
                          False           → force CPU.
        """
        self._languages = languages or ["en"]
        self._gpu       = gpu          # None = auto-detect
        self._reader    = None

    # ── Lazy initialisation ───────────────────────────────────

    def _initialize_reader(self) -> None:
        """
        Instantiate the EasyOCR Reader. Called once on first extract().

        GPU auto-detection (when self._gpu is None):
            Attempt GPU initialisation. If it raises (no CUDA, driver
            mismatch, etc.) fall back silently to CPU. A log warning is
            emitted so operators are aware of the degradation.
        """
        if self._reader is not None:
            return

        if self._gpu is None:
            try:
                self._reader = easyocr.Reader(self._languages, gpu=True)
                logger.info("OcrExtractor: initialised with GPU.")
            except Exception as gpu_err:
                logger.warning(
                    "OcrExtractor: GPU initialisation failed (%s) — "
                    "falling back to CPU.", gpu_err
                )
                self._reader = easyocr.Reader(self._languages, gpu=False)
        else:
            self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
            logger.info(
                "OcrExtractor: initialised with %s.",
                "GPU" if self._gpu else "CPU",
            )

    # ── Public API ────────────────────────────────────────────

    def extract(self, image_path: str) -> list:
        """
        Extract text lines from an image file using EasyOCR.

        Args:
            image_path : Absolute path to the image (.png / .jpg / etc.)

        Returns:
            List[str] — one entry per detected text region, ordered
            top-to-bottom, filtered to confidence >= CONFIDENCE_THRESHOLD.

        Raises:
            FileNotFoundError : image_path does not exist on disk.
            RuntimeError      : EasyOCR reader failed to initialise or
                                failed during text detection.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        try:
            self._initialize_reader()
        except Exception as e:
            raise RuntimeError(
                f"OcrExtractor: reader initialisation failed: {e}"
            ) from e

        try:
            # EasyOCR result format: [(bbox, text, confidence), ...]
            results = self._reader.readtext(image_path)
        except Exception as e:
            raise RuntimeError(
                f"OcrExtractor: text detection failed for '{image_path}': {e}"
            ) from e

        return [
            text
            for _, text, confidence in results
            if confidence >= self.CONFIDENCE_THRESHOLD
        ]