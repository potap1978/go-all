# Kaskad: IKEv2 for Windows 10/11 (built-in client)
# Run in Windows PowerShell as administrator: powershell -ExecutionPolicy Bypass -File .\kaskad-ikev2.ps1
$ErrorActionPreference = 'Stop'
$name = 'Kaskad IKEv2'
$server = '203.0.113.10'
# The CA of the node -> Trusted Root Certification Authorities of this computer
$pem = @'
-----BEGIN CERTIFICATE-----
MIIDeDCCAmCgAwIBAgIQP6nB0rTl9gcYKTpLXG1+jzANBgkqhkiG9w0BAQsFADAu
MSwwKgYDVQQDDCNLYXNrYWQgSUtFdjIgQ0EgMjAzLjAuMTEzLjEwIDdmM2ExYzAe
Fw0yNjEwMDQyMDQyMThaFw0zNjEwMDEyMDQyMThaMC4xLDAqBgNVBAMMI0thc2th
ZCBJS0V2MiBDQSAyMDMuMC4xMTMuMTAgN2YzYTFjMIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEAzDoBzmziBwJTh4tc9SSVeCTaHTB4TpZUYW4Q6uNdGiiV
WSVLDVIILqnjkIVkyvqcDtG6OuAWU3builb9zofqEHh5U83UJVrK682TQglCoeWO
IBtSXpTq/Ga6u958P1lKJkrvpedua1+31h4qKBGgHN1gHjoIaMFU4gE9D5s3d7Yd
R8bNrq13uN/+XG+6bdmj70f5ZY7TKS1bRD3J+zvwBACxR9w3SwWIkGEIZdBXSOQo
v4aFj1DFDnIpzLQRk+8nG9zSh03mEra5LK0020eYrYaMGJwJ3AMfO+IYCsC6OIrl
rLQlMOBRhb8pICiitBP+gGvg4nh2w8EkOfG4+a253wIDAQABo4GRMIGOMBIGA1Ud
EwEB/wQIMAYBAf8CAQAwDgYDVR0PAQH/BAQDAgEGMB0GA1UdJQQWMBQGCCsGAQUF
BwMBBggrBgEFBQgCAjAdBgNVHQ4EFgQUrPmyMRFeMy+Okf/GrQf5fdU39+cwKgYD
VR0eAQH/BCAwHqAcMAqHCMsAcQr/////MA6CDDIwMy4wLjExMy4xMDANBgkqhkiG
9w0BAQsFAAOCAQEAclrZ212K3tydf016mFVXvSinlTD2haPggF6KsSQUcpDuxcgx
9Ra2hapFnhV8uCphBgeTQIPp6gf98c4DR970MqCetyiz/t7vjsFD8hF55CI9BCU7
KyFJ6Lp4bLfZ7InVoObL2C8dRQxIqy52Ap6aelf79T09F/u8j9eKbxjG8ysp91vY
I42N0JDQgfNfgTn6JZHTUYCpYl5WubxN0qn43x6KwN3zbRtITkRvMSOhc5c+LhbC
D3maoeVlOgT9hmkwCyhMmC6rJP10FKxkqe8Dt8Qr/IPvBWmDEARs+QFd3nyQFFB+
+rviBFH6BPb2Nsn/01xNSIF2mF10si0FemLqSA==
-----END CERTIFICATE-----
'@
$file = Join-Path $env:TEMP 'kaskad-ikev2-ca.cer'
[IO.File]::WriteAllText($file, $pem)
Import-Certificate -FilePath $file -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Remove-Item $file
# The connection for every user of this computer (an older one with this name is replaced)
Remove-VpnConnection -AllUserConnection -Name $name -Force -ErrorAction SilentlyContinue
Add-VpnConnection -AllUserConnection -Name $name -ServerAddress $server -TunnelType Ikev2 -AuthenticationMethod Eap -EncryptionLevel Required -RememberCredential -Force
# The server's first proposal (Windows alone offers DH group 2 and SHA-1)
Set-VpnConnectionIPsecConfiguration -AllUserConnection -ConnectionName $name -AuthenticationTransformConstants GCMAES256 -CipherTransformConstants GCMAES256 -EncryptionMethod AES256 -IntegrityCheckMethod SHA256 -DHGroup Group14 -PfsGroup None -Force
Write-Host "Done: Settings > Network & Internet > VPN > $name > Connect (login and password of the device)."
