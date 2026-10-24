{
  description = "archivvit: a local, read-only archive of a few subreddits";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        archivvit = pkgs.callPackage ./nix/package.nix { };
        default = archivvit;
      });

      nixosModules.default = import ./nix/module.nix { inherit self; };

      # `nix flake check`: the package (its build runs pytest) and the module in a VM.
      checks = forAllSystems (pkgs: {
        package = self.packages.${pkgs.stdenv.hostPlatform.system}.archivvit;
        vm = import ./nix/vm-test.nix { inherit self pkgs; };
      });

      devShells = forAllSystems (
        pkgs:
        let
          pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.archivvit;
        in
        {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: pkg.dependencies ++ [ ps.pytest ]))
            ];
            shellHook = ''
              export PLAYWRIGHT_BROWSERS_PATH=${pkg.browsers}
              export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
              export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
            '';
          };
        }
      );

      formatter = forAllSystems (pkgs: pkgs.nixfmt-rfc-style);
    };
}
