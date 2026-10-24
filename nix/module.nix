# services.archivvit: the web UI and scraper as a systemd service.
{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.archivvit;
in
{
  options.services.archivvit = {
    enable = lib.mkEnableOption "archivvit, a local read-only archive of a few subreddits";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage "${self}/nix/package.nix" { };
      defaultText = lib.literalExpression "archivvit built with the system's nixpkgs";
      description = "The archivvit package.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address to listen on. The UI has no login: keep it local or behind a private proxy.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "Port to listen on.";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/archivvit";
      description = "The archive (archive.sqlite), browser cookies and settings.json, edited from the UI.";
    };

    scheduler = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Scrape on the interval set in the UI. When false, only the UI is served.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "archivvit";
      description = "User to run as. `archivvit` is created when left as is.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "archivvit";
      description = "Group to run as. `archivvit` is created when left as is.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users = lib.mkIf (cfg.user == "archivvit") {
      archivvit = {
        isSystemUser = true;
        group = cfg.group;
        home = cfg.dataDir;
      };
    };
    users.groups = lib.mkIf (cfg.group == "archivvit") { archivvit = { }; };

    systemd.tmpfiles.settings."10-archivvit".${cfg.dataDir}.d = {
      inherit (cfg) user group;
      mode = "0750";
    };

    systemd.services.archivvit = {
      description = "archivvit";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      unitConfig.RequiresMountsFor = [ cfg.dataDir ];

      environment = {
        # Headless Chromium wants a writable home for its caches.
        HOME = "/var/cache/archivvit";
        XDG_CACHE_HOME = "/var/cache/archivvit";
        PYTHONUNBUFFERED = "1";
      };

      serviceConfig = {
        ExecStart = lib.concatStringsSep " " (
          [
            (lib.getExe cfg.package)
            "--data"
            cfg.dataDir
            "serve"
            "--host"
            cfg.host
            "--port"
            (toString cfg.port)
          ]
          ++ lib.optional (!cfg.scheduler) "--no-scheduler"
        );
        User = cfg.user;
        Group = cfg.group;
        Restart = "on-failure";
        RestartSec = 5;
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        LockPersonality = true;
        ReadWritePaths = [ cfg.dataDir ];
        CacheDirectory = "archivvit";
        # Not MemoryDenyWriteExecute: Chromium's JavaScript engine needs W+X memory.
      };
    };
  };
}
