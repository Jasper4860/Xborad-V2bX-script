# Safe upgrade and certificate renewal
- Download the operator-selected version over verified HTTPS, verify its trusted SHA-256, and stage all files before any replacement. Exercise invalid input, interrupted downloads, successful upgrade, preservation of configuration and service recovery. Keep existing config and certificate paths. No production deployment.
- Extend the existing certificate manager with HTTP-01 webroot and standalone detection, retain explicit existing certificate mode, and validate renewal results before touching the service. No Cloudflare secret needed in the normal workflow; no firewall changes. Test with mocked commands and temporary trees.
- Verify shell syntax and complete fixture suite; disclose live Debian/ACME verification not performed.
