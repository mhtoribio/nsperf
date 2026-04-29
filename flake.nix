{
  description = "nsperf UDP performance and tracing tool";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = forAllSystems (system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              go
              golangci-lint
              gopls
              gotools
              python3
              python3Packages.pytest
              shellcheck
            ];

            shellHook = ''
              echo "nsperf development shell"
              echo "  go test ./..."
              echo "  go run ./cmd/nsperf --help"
              echo "  python3 tools/analyze.py --help"
            '';
          };
        });
    };
}
