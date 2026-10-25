from unittest.mock import Mock, patch, MagicMock
from trading_gym.core.monitoring_app.exporter import MetricsHandler, main


class TestMetricsHandler:
    def test_do_GET_metrics(self):
        # Setup mocks
        request = MagicMock()
        client_address = ("127.0.0.1", 12345)
        server = MagicMock()
        server.bearer_token = None

        mock_wfile = MagicMock()

        with patch(
            "trading_gym.core.monitoring_app.exporter.generate_latest", return_value=b"# HELP metrics"
        ):
            with patch("trading_gym.core.monitoring_app.exporter.get_metrics_instance"):
                # Patch BaseHTTPRequestHandler methods to intercept calls
                with patch(
                    "http.server.BaseHTTPRequestHandler.send_response"
                ) as mock_send_response:
                    with patch("http.server.BaseHTTPRequestHandler.send_header"):
                        with patch("http.server.BaseHTTPRequestHandler.end_headers"):

                            # Patch handle() to prevent auto-execution during init
                            with patch("http.server.BaseHTTPRequestHandler.handle"):
                                handler = MetricsHandler(
                                    request, client_address, server
                                )
                                handler.request_version = (
                                    "HTTP/1.1"  # Fix: Set request_version
                                )
                                handler.wfile = mock_wfile
                                handler.path = "/metrics"
                                handler.headers = {}

                                handler.do_GET()

                                mock_send_response.assert_called_with(200)
                                mock_wfile.write.assert_called_with(b"# HELP metrics")

    def test_do_GET_health(self):
        request = MagicMock()
        client_address = ("127.0.0.1", 12345)
        server = MagicMock()
        server.bearer_token = None
        mock_wfile = MagicMock()

        with patch(
            "http.server.BaseHTTPRequestHandler.send_response"
        ) as mock_send_response:
            with patch("http.server.BaseHTTPRequestHandler.handle"):
                handler = MetricsHandler(request, client_address, server)
                handler.request_version = "HTTP/1.1"  # Fix
                handler.wfile = mock_wfile
                handler.path = "/health"

                handler.do_GET()

                mock_send_response.assert_called_with(200)
                mock_wfile.write.assert_called_with(b"OK\n")

    def test_do_GET_404(self):
        request = MagicMock()
        client_address = ("127.0.0.1", 12345)
        server = MagicMock()
        mock_wfile = MagicMock()

        with patch(
            "http.server.BaseHTTPRequestHandler.send_response"
        ) as mock_send_response:
            with patch("http.server.BaseHTTPRequestHandler.handle"):
                handler = MetricsHandler(request, client_address, server)
                handler.request_version = "HTTP/1.1"  # Fix
                handler.wfile = mock_wfile
                handler.path = "/unknown"

                handler.do_GET()

                mock_send_response.assert_called_with(404)


@patch("trading_gym.core.monitoring_app.exporter.MetricsServer")
def test_main(mock_server_cls):
    mock_server = Mock()
    mock_server_cls.return_value = mock_server
    mock_server.serve_forever.side_effect = KeyboardInterrupt

    with patch("signal.signal"):
        main(port=9099, host="localhost")

        mock_server_cls.assert_called_with(
            ("localhost", 9099), MetricsHandler, bearer_token=None
        )
        mock_server.server_close.assert_called()
