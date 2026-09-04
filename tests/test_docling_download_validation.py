"""
🧪 Test Suite: Docling Download Validation (SOTA Fixes)

Tests für die neuen Download-Validierungsmethoden:
- Prädiktive Validierung (HEAD-Request)
- Adaptive Timeouts
- Retry-Logik mit Backoff
- Post-Download Validierung (Magic Bytes)
- Error Recovery

Behebt Root Causes:
1. HTTP-Error-Seiten-Misklassifikation (0.2 KB Bug)
2. Transiente Download-Fehler ohne Retry
3. Unvollständige Downloads ohne Validierung
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock
import io

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.docling_processor import DoclingProcessor, DoclingResult


class TestDownloadValidation:
    """Test Suite für Download-Validierung"""
    
    @pytest.fixture
    def processor(self):
        """Create processor instance"""
        return DoclingProcessor.get_instance()
    
    # ═══════════════════════════════════════════════════════════════════
    # Test 1: Prädiktive Validierung
    # ═══════════════════════════════════════════════════════════════════
    
    def test_predictive_validation_valid_pdf(self, processor):
        """Test: Prädiktive Validierung erkennt gültiges PDF"""
        with patch('requests.head') as mock_head:
            mock_response = Mock()
            mock_response.headers = {
                'Content-Length': '1000000',  # 1 MB
                'Content-Type': 'application/pdf',
                'Server': 'Apache/2.4.41',
            }
            mock_response.raise_for_status = Mock()
            mock_head.return_value = mock_response
            
            result = processor._validate_url_download(
                'http://example.com/document.pdf',
                'PDF',
                timeout=10
            )
            
            assert result['valid'] is True
            assert result['content_length_mb'] == pytest.approx(1.0, 0.01)
            assert result['expected_size_bytes'] == 1000000
    
    def test_predictive_validation_too_small_file(self, processor):
        """Test: Prädiktive Validierung erkennt zu kleine Dateien (Fehlerseiten)"""
        with patch('requests.head') as mock_head:
            mock_response = Mock()
            mock_response.headers = {
                'Content-Length': '200',  # Nur 200 Bytes = typische HTML-Fehlerseite
                'Content-Type': 'application/pdf',
                'Server': 'Apache/2.4.41',
            }
            mock_response.raise_for_status = Mock()
            mock_head.return_value = mock_response
            
            result = processor._validate_url_download(
                'http://example.com/document.pdf',
                'PDF',
                timeout=10
            )
            
            assert result['valid'] is False
            assert 'too small' in result['reason'].lower()
    
    def test_predictive_validation_html_response(self, processor):
        """Test: Prädiktive Validierung erkennt HTML statt PDF"""
        with patch('requests.head') as mock_head:
            mock_response = Mock()
            mock_response.headers = {
                'Content-Length': '2000',
                'Content-Type': 'text/html; charset=utf-8',
                'Server': 'nginx/1.18.0',
            }
            mock_response.raise_for_status = Mock()
            mock_head.return_value = mock_response
            
            result = processor._validate_url_download(
                'http://example.com/document.pdf',
                'PDF',
                timeout=10
            )
            
            assert result['valid'] is False
            assert 'html' in result['reason'].lower()
    
    def test_predictive_validation_too_large_file(self, processor):
        """Test: Prädiktive Validierung erkennt zu große Dateien"""
        with patch('requests.head') as mock_head:
            mock_response = Mock()
            mock_response.headers = {
                'Content-Length': str(300 * 1024 * 1024),  # 300 MB
                'Content-Type': 'application/pdf',
                'Server': 'Apache/2.4.41',
            }
            mock_response.raise_for_status = Mock()
            mock_head.return_value = mock_response
            
            result = processor._validate_url_download(
                'http://example.com/document.pdf',
                'PDF',
                timeout=10
            )
            
            assert result['valid'] is False
            assert 'too large' in result['reason'].lower()
    
    # ═══════════════════════════════════════════════════════════════════
    # Test 2: Adaptive Timeouts
    # ═══════════════════════════════════════════════════════════════════
    
    def test_adaptive_timeout_small_file(self, processor):
        """Test: Adaptive Timeout für kleine Dateien"""
        timeout = processor._calculate_adaptive_timeout(5.0, base_timeout=30)
        assert timeout == 30
    
    def test_adaptive_timeout_medium_file(self, processor):
        """Test: Adaptive Timeout für mittlere Dateien"""
        timeout = processor._calculate_adaptive_timeout(30.0, base_timeout=30)
        assert timeout == 60
    
    def test_adaptive_timeout_large_file(self, processor):
        """Test: Adaptive Timeout für große Dateien"""
        timeout = processor._calculate_adaptive_timeout(75.0, base_timeout=30)
        assert timeout == 120
    
    def test_adaptive_timeout_very_large_file(self, processor):
        """Test: Adaptive Timeout für sehr große Dateien"""
        timeout = processor._calculate_adaptive_timeout(150.0, base_timeout=30)
        assert timeout == 180
    
    # ═══════════════════════════════════════════════════════════════════
    # Test 3: Retry-Logik
    # ═══════════════════════════════════════════════════════════════════
    
    def test_download_with_retry_success_first_attempt(self, processor):
        """Test: Download erfolgreich beim ersten Versuch"""
        with patch('requests.get') as mock_get:
            # Erstelle Mock-Response mit PDF-Daten
            mock_response = Mock()
            mock_response.iter_content = Mock(return_value=[b"%PDF-1.4\n", b"...\n"])
            mock_response.close = Mock()
            mock_get.return_value = mock_response
            
            with patch('tempfile.NamedTemporaryFile') as mock_temp:
                mock_tmp = MagicMock()
                mock_tmp.name = '/tmp/test_pdf.pdf'
                mock_tmp.__enter__.return_value = mock_tmp
                mock_temp.return_value = mock_tmp
                
                with patch('os.path.exists', return_value=True):
                    result = processor._download_with_retry(
                        'http://example.com/doc.pdf',
                        'PDF',
                        '.pdf',
                        timeout=30
                    )
                    
                    assert result == '/tmp/test_pdf.pdf'
                    mock_get.assert_called_once()
    
    def test_download_with_retry_transient_failure_then_success(self, processor):
        """Test: Download mit transientem Fehler, dann Erfolg"""
        with patch('requests.get') as mock_get:
            # Erster Versuch: ConnectionError
            # Zweiter Versuch: Erfolg
            mock_response = Mock()
            mock_response.iter_content = Mock(return_value=[b"%PDF-1.4\n"])
            mock_response.close = Mock()
            
            import requests
            mock_get.side_effect = [
                requests.exceptions.ConnectionError("Connection reset"),
                mock_response,
            ]
            
            with patch('time.sleep'):  # Skip actual sleep
                with patch('tempfile.NamedTemporaryFile') as mock_temp:
                    mock_tmp = MagicMock()
                    mock_tmp.name = '/tmp/test_pdf.pdf'
                    mock_tmp.__enter__.return_value = mock_tmp
                    mock_temp.return_value = mock_tmp
                    
                    with patch('os.path.exists', return_value=True):
                        result = processor._download_with_retry(
                            'http://example.com/doc.pdf',
                            'PDF',
                            '.pdf',
                            timeout=30
                        )
                        
                        assert result == '/tmp/test_pdf.pdf'
                        assert mock_get.call_count == 2
    
    def test_download_with_retry_all_attempts_fail(self, processor):
        """Test: Download schlägt bei allen Versuchen fehl"""
        with patch('requests.get') as mock_get:
            import requests
            mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
            
            with patch('time.sleep'):  # Skip actual sleep
                result = processor._download_with_retry(
                    'http://example.com/doc.pdf',
                    'PDF',
                    '.pdf',
                    timeout=30
                )
                
                assert result is None
                assert mock_get.call_count == 3  # MAX_RETRIES = 3
    
    # ═══════════════════════════════════════════════════════════════════
    # Test 4: Post-Download Validierung
    # ═══════════════════════════════════════════════════════════════════
    
    def test_post_download_validation_valid_pdf(self, processor):
        """Test: Post-Download Validierung erkennt gültiges PDF"""
        # Erstelle temporäre PDF-Datei
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(b"%PDF-1.4\nTest PDF content...")
            tmp_path = tmp.name
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'PDF',
                expected_size_bytes=500
            )
            
            assert result['valid'] is True
            assert result['actual_size_bytes'] > 0
            assert result['magic_bytes'].startswith('25504446')  # %PDF in hex
        finally:
            os.unlink(tmp_path)
    
    def test_post_download_validation_empty_file(self, processor):
        """Test: Post-Download Validierung erkennt leere Datei"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp_path = tmp.name
            # Datei ist leer
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'PDF',
                expected_size_bytes=1000000
            )
            
            assert result['valid'] is False
            assert 'empty' in result['reason'].lower()
        finally:
            os.unlink(tmp_path)
    
    def test_post_download_validation_wrong_magic_bytes(self, processor):
        """Test: Post-Download Validierung erkennt falsche Magic Bytes"""
        # Erstelle Datei mit HTML-Inhalt (falsch für PDF)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(b"<html><body>Error 404</body></html>")
            tmp_path = tmp.name
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'PDF',
                expected_size_bytes=1000000
            )
            
            assert result['valid'] is False
            assert 'magic bytes' in result['reason'].lower()
        finally:
            os.unlink(tmp_path)
    
    def test_post_download_validation_size_mismatch(self, processor):
        """Test: Post-Download Validierung warnt bei Größenmissatch"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(b"%PDF-1.4\n" + b"Small")
            tmp_path = tmp.name
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'PDF',
                expected_size_bytes=10000000  # 10 MB expected, aber nur ~20 Bytes
            )
            
            # Sollte nur warnen, nicht fehlschlagen (da Magic Bytes korrekt)
            assert result['valid'] is True
        finally:
            os.unlink(tmp_path)
    
    def test_post_download_validation_docx(self, processor):
        """Test: Post-Download Validierung für DOCX (ZIP-Format)"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
            # DOCX ist ein ZIP-Format
            tmp.write(b"PK\x03\x04" + b"...[ZIP content]...")
            tmp_path = tmp.name
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'DOCX',
                expected_size_bytes=50000
            )
            
            assert result['valid'] is True
        finally:
            os.unlink(tmp_path)
    
    def test_post_download_validation_image_png(self, processor):
        """Test: Post-Download Validierung für PNG-Bilder"""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            # PNG Magic Bytes
            tmp.write(b"\x89PNG\r\n\x1a\n" + b"...[PNG data]...")
            tmp_path = tmp.name
        
        try:
            result = processor._validate_downloaded_file(
                tmp_path,
                'PNG',
                expected_size_bytes=100000
            )
            
            assert result['valid'] is True
        finally:
            os.unlink(tmp_path)


class TestIntegrationScenarios:
    """Integration Tests für reale Download-Szenarien"""
    
    @pytest.fixture
    def processor(self):
        """Create processor instance"""
        return DoclingProcessor.get_instance()
    
    def test_scenario_error_page_misclassified_as_pdf(self, processor):
        """
        🔴 Scenario: Server gibt HTML-Fehlerseite statt PDF zurück
        
        Root Cause des 0.2KB-Bugs:
        - User stellt URL bereit (sieht nach PDF aus)
        - Server ist down/überlastet, gibt 403/500 mit HTML zurück
        - Alte Logik: Blindes Schreiben → 0.2 KB HTML-Datei
        - Neue Logik: Drei Ebenen fangen das:
          1. Prädiktive Validierung (HEAD-Request)
          2. Size-Check (zu klein)
          3. Magic Byte Check (falsche Format)
        """
        with patch('requests.head') as mock_head:
            with patch('requests.get') as mock_get:
                # HEAD-Request erkennt HTML
                mock_head_resp = Mock()
                mock_head_resp.headers = {
                    'Content-Length': '300',  # Kleine HTML-Seite
                    'Content-Type': 'text/html',
                }
                mock_head_resp.raise_for_status = Mock()
                mock_head.return_value = mock_head_resp
                
                # Aber GET würde trotzdem aufgerufen (falls HEAD fehlschlägt)
                mock_get_resp = Mock()
                mock_get_resp.iter_content = Mock(return_value=[b"<html>403 Forbidden</html>"])
                mock_get_resp.close = Mock()
                mock_get.return_value = mock_get_resp
                
                result = processor._validate_url_download(
                    'http://example.com/doc.pdf',
                    'PDF',
                    timeout=10
                )
                
                # ✅ Prädiktive Validierung sollte Fehler erkennen
                assert result['valid'] is False


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
