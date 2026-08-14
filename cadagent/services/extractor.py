"""
================================================

Feature Extraction Service

BREP feature extraction service
Wraps Scripts.main_extractor, adding timeout control and error handling

================================================
"""

import os
import json
import tempfile
import logging
import threading
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)


# ==============================================================================
# Feature Extraction Class
# ==============================================================================

class FeatureExtractor:
    """
    Wrapper class for BREP feature extraction with timeout support.
    """

    def __init__(self, timeout: int = 60, progress_callback=None):
        """
        Initialize the feature extractor.

        Args:
            timeout: Maximum time in seconds for extraction
            progress_callback: Optional (done, total) callback forwarded to the
                gear Z-axis scan for UI progress. None keeps legacy behaviour.
        """
        self.timeout = timeout
        self._progress_callback = progress_callback
        self._result = None
        self._error = None
        self._finished = False

    def _extract_in_thread(self, brep_path: str, output_path: str, verbose: bool = False):
        """Internal method to run extraction in a separate thread."""
        try:
            from cadagent.Scripts.main_extractor import extract_features

            result = extract_features(
                brep_path,
                output_path,
                verbose=verbose,
                progress_callback=self._progress_callback,
            )

            # Also read the JSON file if created
            if os.path.exists(output_path):
                with open(output_path, 'r', encoding='utf-8') as f:
                    self._result = json.load(f)
            else:
                self._result = result

        except Exception as e:
            logger.error(f"Feature extraction failed: {e}")
            self._error = str(e)
        finally:
            self._finished = True

    def extract(self, brep_path: str, verbose: bool = False) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """
        Extract features from a BREP file with timeout support.

        Args:
            brep_path: Path to the BREP file
            verbose: Enable verbose logging

        Returns:
            Tuple of (success: bool, result: dict or None, error: str or None)
        """
        self._result = None
        self._error = None
        self._finished = False

        # Create temporary output path
        temp_dir = tempfile.mkdtemp(prefix="feature_extraction_")
        output_path = os.path.join(temp_dir, "extraction_result.json")

        # Start extraction in a separate thread
        thread = threading.Thread(
            target=self._extract_in_thread,
            args=(brep_path, output_path, verbose)
        )
        thread.daemon = True
        thread.start()

        # Wait for completion with timeout
        import time
        start_time = time.time()

        while thread.is_alive():
            if time.time() - start_time > self.timeout:
                self._error = f"Feature extraction timed out after {self.timeout} seconds"
                logger.error(self._error)
                return False, None, self._error
            time.sleep(0.1)

        # Clean up temp directory
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rmdir(temp_dir)
        except:
            pass

        if self._error:
            return False, None, self._error

        return True, self._result, None


# ==============================================================================
# Convenience Functions
# ==============================================================================

def extract_features(brep_path: str, timeout: int = 60, verbose: bool = False,
                     progress_callback=None) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Extract features from a BREP file.

    Args:
        brep_path: Path to the BREP file
        timeout: Maximum time in seconds for extraction
        verbose: Enable verbose logging
        progress_callback: Optional (done, total) callback forwarded to the gear
            Z-axis scan for UI progress. None keeps legacy behaviour.

    Returns:
        Tuple of (success: bool, result: dict or None, error: str or None)
    """
    extractor = FeatureExtractor(timeout=timeout, progress_callback=progress_callback)
    return extractor.extract(brep_path, verbose=verbose)
