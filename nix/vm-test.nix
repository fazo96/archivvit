# The module in a VM: the service starts and serves the UI, a pass without internet is
# recorded as failed instead of crashing it, and headless Chromium runs inside the
# service's sandbox (against a local page that sets the cookies Reddit's check would).
{ self, pkgs }:
pkgs.testers.runNixOSTest {
  name = "archivvit";

  nodes.machine =
    { config, pkgs, ... }:
    let
      cookiePage = pkgs.writeText "cookies.py" ''
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Set-Cookie", "csrf_token=abc; Path=/")
                self.send_header("Set-Cookie", "loid=xyz; Path=/")
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body>ok</body></html>")

        HTTPServer(("127.0.0.1", 8099), H).serve_forever()
      '';
    in
    {
      imports = [ self.nixosModules.default ];
      services.archivvit.enable = true;
      virtualisation.memorySize = 2048;

      systemd.services.cookie-page = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${cookiePage}";
      };

      # Same sandbox as the service, running the browser once.
      systemd.services.archivvit-browser-check = {
        after = [ "cookie-page.service" ];
        environment = config.systemd.services.archivvit.environment;
        serviceConfig = config.systemd.services.archivvit.serviceConfig // {
          Type = "oneshot";
          Restart = "no";
          ExecStart = "${pkgs.lib.getExe config.services.archivvit.package} --data ${config.services.archivvit.dataDir} session --url http://127.0.0.1:8099/";
        };
      };
    };

  testScript = ''
    machine.wait_for_unit("archivvit.service")
    machine.wait_for_open_port(8080)
    machine.succeed("curl -sf http://127.0.0.1:8080/settings | grep -q 'Run now'")
    machine.succeed("curl -sf http://127.0.0.1:8080/r/NixOS/ | grep -q 'Nothing archived yet'")

    # Save settings through the form, as the UI does.
    machine.succeed(
        "curl -sf -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8080/settings "
        "-d subreddits=NixOS%0Alinux -d posts_per_sub=5 -d scrape_interval_minutes=30 "
        "-d refetch_hours=2 -d hot_window_hours=24 -d max_thread_requests=10 -d min_delay=1 -d max_delay=2 -d retention_days=30 "
        "| grep -q 302"
    )
    machine.succeed("grep -q linux /var/lib/archivvit/settings.json")

    # No internet in the VM: the pass fails, the service keeps running and shows why.
    machine.succeed("curl -sf -X POST -o /dev/null http://127.0.0.1:8080/run")
    machine.wait_until_succeeds("curl -sf http://127.0.0.1:8080/settings | grep -q 'with errors'", timeout=180)
    machine.succeed("systemctl is-active archivvit.service")

    machine.wait_for_open_port(8099)
    out = machine.succeed("systemctl start archivvit-browser-check.service && journalctl -u archivvit-browser-check -o cat")
    assert "csrf_token loid" in out, out
  '';
}
