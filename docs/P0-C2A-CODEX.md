# P0-C2A Codex CLI baseline

## Baseline and source

- Baseline commit: ddf200c2dbd94f9925e1e201270b68ca111d46e3
- Release page: https://github.com/openai/codex/releases/tag/rust-v0.144.3
- Final asset URL: https://github.com/openai/codex/releases/download/rust-v0.144.3/codex-x86_64-pc-windows-msvc.exe.zip
- ZIP size: 114556315 bytes
- Official SHA-256: 5490114d8684b30f91e6e6f7b1238b2544fa3b957e42c9836aa959e8f563c01f
- Local SHA-256: 5490114d8684b30f91e6e6f7b1238b2544fa3b957e42c9836aa959e8f563c01f
- ZIP digest match: True

The ZIP container was accepted by its official Release SHA-256. Authenticode validation was applied to every extracted EXE, not to the ZIP container.

## ZIP central-directory inventory

| Entry | Uncompressed bytes | Compressed bytes | External attributes |
|---|---:|---:|---:|
| codex-command-runner.exe | 1271600 | 531333 | 32 |
| codex-windows-sandbox-setup.exe | 8817968 | 3395276 | 32 |
| codex-x86_64-pc-windows-msvc.exe | 341307184 | 110629174 | 32 |

The archive contained exactly these three ordinary files. No duplicate, absolute, drive-prefixed, ADS, traversal, directory, symlink, or reparse entry was accepted.

## Extracted EXE hashes and Authenticode

### codex-command-runner.exe

- Size: 1271600 bytes
- SHA-256: 9806824e11aacfc2fc41c5aec9413cb64f755cafd982f49170fa4f659500444a
- Status: Valid
- Status message: 签名已通过验证。
- Signer subject: CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", L=San Francisco, S=California, C=US
- Signer issuer: CN=Microsoft ID Verified CS AOC CA 04, O=Microsoft Corporation, C=US
- Signer thumbprint: 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3
- Timestamper subject: CN=Microsoft Public RSA Time Stamping Authority, OU=nShield TSS ESN:A500-05E0-D947, OU=Microsoft America Operations, O=Microsoft Corporation, L=Redmond, S=Washington, C=US
- Timestamper issuer: CN=Microsoft Public RSA Timestamping CA 2020, O=Microsoft Corporation, C=US
- Timestamper thumbprint: FF73F729152A9059805E5E0832449D996EF60411

### codex-windows-sandbox-setup.exe

- Size: 8817968 bytes
- SHA-256: 7efa768607d8e3f3fbf8f018c7a3454695fae718984345125bab387a863f089f
- Status: Valid
- Status message: 签名已通过验证。
- Signer subject: CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", L=San Francisco, S=California, C=US
- Signer issuer: CN=Microsoft ID Verified CS AOC CA 04, O=Microsoft Corporation, C=US
- Signer thumbprint: 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3
- Timestamper subject: CN=Microsoft Public RSA Time Stamping Authority, OU=nShield TSS ESN:7800-05E0-D947, OU=Microsoft America Operations, O=Microsoft Corporation, L=Redmond, S=Washington, C=US
- Timestamper issuer: CN=Microsoft Public RSA Timestamping CA 2020, O=Microsoft Corporation, C=US
- Timestamper thumbprint: FD2F31399C42510141364ACDF168A28F5C79CBC3

### codex-x86_64-pc-windows-msvc.exe

- Size: 341307184 bytes
- SHA-256: e5dcc9f9b08102c58596af85345f689a69fd53a87d8d408bdc0fcdaf99fcf6e3
- Status: Valid
- Status message: 签名已通过验证。
- Signer subject: CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", L=San Francisco, S=California, C=US
- Signer issuer: CN=Microsoft ID Verified CS AOC CA 04, O=Microsoft Corporation, C=US
- Signer thumbprint: 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3
- Timestamper subject: CN=Microsoft Public RSA Time Stamping Authority, OU=nShield TSS ESN:7800-05E0-D947, OU=Microsoft America Operations, O=Microsoft Corporation, L=Redmond, S=Washington, C=US
- Timestamper issuer: CN=Microsoft Public RSA Timestamping CA 2020, O=Microsoft Corporation, C=US
- Timestamper thumbprint: FD2F31399C42510141364ACDF168A28F5C79CBC3

All three signatures were strictly Valid before toolchain creation.

## Toolchain inventory and recheck

| File | Bytes | SHA-256 | Authenticode | Signer thumbprint |
|---|---:|---|---|---|
| codex.exe | 341307184 | e5dcc9f9b08102c58596af85345f689a69fd53a87d8d408bdc0fcdaf99fcf6e3 | Valid | 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3 |
| codex-command-runner.exe | 1271600 | 9806824e11aacfc2fc41c5aec9413cb64f755cafd982f49170fa4f659500444a | Valid | 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3 |
| codex-windows-sandbox-setup.exe | 8817968 | 7efa768607d8e3f3fbf8f018c7a3454695fae718984345125bab387a863f089f | Valid | 6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3 |

Each toolchain SHA-256 matched its corresponding verify file and each copied EXE remained Authenticode Valid.

## Isolated process environment

- Child-process-only CODEX_HOME: E:\houdini-intelligence-agent\.runtime\codex-home
- Child-process-only TEMP: E:\houdini-intelligence-agent\.runtime\codex-home\tmp
- Child-process-only TMP: E:\houdini-intelligence-agent\.runtime\codex-home\tmp
- No setx, User environment, Machine environment, global PATH, AppData, WindowsApps, registry, or user configuration was modified.

## CLI probe results

### codex.exe --version

- Exit code: 0

~~~~text
codex-cli 0.144.3
~~~~

### codex.exe app-server --help

- Exit code: 0

~~~~text
[experimental] Run the app server or related tooling

Usage: codex app-server [OPTIONS] [COMMAND]

Commands:
  daemon                Manage the local app-server daemon
  proxy                 Proxy stdio bytes to the running app-server control socket
  generate-ts           [experimental] Generate TypeScript bindings for the app server protocol
  generate-json-schema  [experimental] Generate JSON Schema for the app server protocol
  help                  Print this message or the help of the given subcommand(s)

Options:
  -c, --config <key=value>
          Override a configuration value that would otherwise be loaded from `~/.codex/config.toml`.
          Use a dotted path (`foo.bar.baz`) to override nested values. The `value` portion is parsed
          as TOML. If it fails to parse as TOML, the raw string is used as a literal.

          Examples: - `-c model="o3"` - `-c 'sandbox_permissions=["disk-full-read-access"]'` - `-c
          shell_environment_policy.inherit=all`

      --enable <FEATURE>
          Enable a feature (repeatable). Equivalent to `-c features.<name>=true`

      --disable <FEATURE>
          Disable a feature (repeatable). Equivalent to `-c features.<name>=false`

      --strict-config
          Error out when config.toml contains fields that are not recognized by this version of
          Codex

      --listen <URL>
          Transport endpoint URL. Supported values: `stdio://` (default), `unix://`, `unix://PATH`,
          `ws://IP:PORT`, `off`

          [default: stdio://]

      --stdio
          Use stdio as the transport (equivalent to `--listen stdio://`)

      --analytics-default-enabled
          Controls whether analytics are enabled by default.

          Analytics are disabled by default for app-server. Users have to explicitly opt in via the
          `analytics` section in the config.toml file.

          However, for first-party use cases like the VSCode IDE extension, we default analytics to
          be enabled by default by setting this flag. Users can still opt out by setting this in
          their config.toml:

          ```toml [analytics] enabled = false ```

          See https://developers.openai.com/codex/config-advanced/#metrics for more details.

      --ws-auth <MODE>
          Websocket auth mode for non-loopback listeners

          [possible values: capability-token, signed-bearer-token]

      --ws-token-file <PATH>
          Absolute path to the capability-token file

      --ws-token-sha256 <HEX>
          Hex-encoded SHA-256 digest of the capability token

      --ws-shared-secret-file <PATH>
          Absolute path to the shared secret file for signed JWT bearer tokens

      --ws-issuer <ISSUER>
          Expected issuer for signed JWT bearer tokens

      --ws-audience <AUDIENCE>
          Expected audience for signed JWT bearer tokens

      --ws-max-clock-skew-seconds <SECONDS>
          Maximum clock skew when validating signed JWT bearer tokens

  -h, --help
          Print help (see a summary with '-h')
~~~~

The help command exited without starting a persistent app-server. Its own help text labels app-server tooling and schema generation as [experimental]; this run did not pass the --experimental flag.

## Stable/default JSON Schema generation

- Command:

~~~~text
E:\houdini-intelligence-agent\.runtime\toolchains\codex\0.144.3\codex.exe app-server generate-json-schema --out E:\houdini-intelligence-agent\schemas\codex-app-server\0.144.3
~~~~

- Exit code: 0
- --experimental used: False
- Output directory: E:\houdini-intelligence-agent\schemas\codex-app-server\0.144.3
- Generated directories: 2
- Generated files: 267

Generated subdirectories:

- v1
- v2

### Schema file manifest

| Relative path | Bytes | SHA-256 |
|---|---:|---|
| ApplyPatchApprovalParams.json | 2657 | 9de5a28a543214033b546db66ad8d34748a949c9878a7e51ec57a99feb2b8e67 |
| ApplyPatchApprovalResponse.json | 3589 | 1b1c691b986c23e8dd91a182e716d0366de6ec69bf62f83f208efa9f4c53e6ee |
| AttestationGenerateParams.json | 118 | b85df8f169cfbca98724df3a5f7550fc4649fa69b23f5160aafdb52e027133a8 |
| AttestationGenerateResponse.json | 277 | f7c8935c414ac74060cbc0fd4b298c022757e2dea50b3d21a7d507cb24d3318d |
| ChatgptAuthTokensRefreshParams.json | 971 | 8ca65b2cf4a991e5149535d987d7dcf3a1cda0f887d6c4bc102ee56f83417456 |
| ChatgptAuthTokensRefreshResponse.json | 406 | 0407a673d393e20836652e1c8449e851dac0b510819712aa05be3a80e92c9115 |
| ClientNotification.json | 431 | a30b3041578845b11add3d07d5a63cd3a12d5d126e87b8c591862b4aeb68d97c |
| ClientRequest.json | 166089 | 6ca3831bee314191a6947ddd092567f8665e3d4235cf502b17eb619da9623d7f |
| codex_app_server_protocol.schemas.json | 560640 | 931a37b27330ceaeec7177860296577f34ff699b2727913e517a9d9843d9ab8e |
| codex_app_server_protocol.v2.schemas.json | 471114 | 461ee13ce8357e7a32ec50eb93b6e110cb206a0e2f63007de4e5fcf04fe81388 |
| CommandExecutionRequestApprovalParams.json | 15185 | c0d99d1e514ac21d6960175c9ffa3443a7e2fe798aa1c3adf3ad30d60cf414c1 |
| CommandExecutionRequestApprovalResponse.json | 3202 | 42010a48dd9ad989171728c30338e1ff8144c31bd33921cbfb5608fd6c85a3b5 |
| DynamicToolCallParams.json | 519 | e36242b331ca665c74993e55abbea381b1c8a961b29a42579029cff1ad26b20d |
| DynamicToolCallResponse.json | 1496 | 50410ccaaefc9871a42fa25ee0c0d4f488a6f0e08c35856842e12d3103410fd1 |
| ExecCommandApprovalParams.json | 3768 | 6b34b7c6c999280f51146c8f44b08a368987c0c3624b02a89904330b0178cd9e |
| ExecCommandApprovalResponse.json | 3590 | 2582a07c4028d06e0ed67c1b7563469882bbc66640237be182d309fe4ad1d1eb |
| FileChangeRequestApprovalParams.json | 968 | 7b465f7c5671adffdc5c339f50799860950307456e2a2b52c5ce1d3018f4babd |
| FileChangeRequestApprovalResponse.json | 1158 | 7ccbd29e5f8840c7c8aa96c5c3b6d52bc71ec5c5d7e1ad05ab958afd44c0c94c |
| FuzzyFileSearchParams.json | 413 | 44cdbbe54965661bef1452223b6096307741fb41e5a5cf5c161c5feb8474f492 |
| FuzzyFileSearchResponse.json | 1332 | 45d5c686e50887d8fb590ecc090e05fb5b0023305682e932b4dc4db4c0f55045 |
| FuzzyFileSearchSessionCompletedNotification.json | 244 | 74f204b562a0c336678bfb1ebb0938636dc16b00e2ad9fdd646663eb0e4301cd |
| FuzzyFileSearchSessionUpdatedNotification.json | 1474 | 7d5028b648196ce8aac7690f6ffe94bda49339f490f3581d50172a412f99f43c |
| JSONRPCError.json | 893 | 05277af4e68caeabae353d1e643d60501c7f2bf2dcc97348e4cde4d0eba83eb4 |
| JSONRPCErrorError.json | 314 | 39da0e7d4fccd44c7e0acd83ad1433c48a9bf64a3ba8f4cee7ebed18ebbbf533 |
| JSONRPCMessage.json | 2888 | 7b819754ee909272f46d45dbd51bc9eb7bab9861905ac1195f1fb5afd7ff5e83 |
| JSONRPCNotification.json | 303 | a8f8bfca7128b6ffbba13905cba6af966f8598c12237b5cb189b81861cb24e92 |
| JSONRPCRequest.json | 1093 | a174dbc58be007346f5a10fe2f8c8f8c14ccb36191dc1ad0fdd8ce1828f8db1c |
| JSONRPCResponse.json | 520 | 94ecf5e81bdbc2af858afad0044b95c7fb4decf77d7fd7d6321324dad79eef57 |
| McpServerElicitationRequestParams.json | 13251 | 12e180a344101863b73a3ba101d12ae14fff1b6eff679b7ccd0769cc22827261 |
| McpServerElicitationRequestResponse.json | 741 | b9658ba208bfc7ecb3139c4d9742608f260e1583bd6cd11666dce289c975d670 |
| PermissionsRequestApprovalParams.json | 7479 | 1d3d82089f06c19f1ab7c5a8790aad7c8ae8cafb3ebcf48458947cedcc201254 |
| PermissionsRequestApprovalResponse.json | 6828 | 60abc8a0d1c1f24be5be8fafbeb8a2bd0c327ba44088c74ba414b31acdfd4489 |
| RequestId.json | 197 | a33325d0eca49631bf935e9a30d02ba542b42d563f8c58955d3e8f14ad6d3ab1 |
| ServerNotification.json | 169066 | 67669e13a8af9449da30acdbd83a6108fd54ed92b79849dee381aece1521937f |
| ServerRequest.json | 48818 | 7c8a2c6fe03d6afdf8a83f91fa5eb55e7ae630fe2e43a167691ab917dccc9556 |
| ToolRequestUserInputParams.json | 1977 | 21e569e32c05d51c1ee5e587730c182b911ede97a4df267f6e4ef24e1717f34e |
| ToolRequestUserInputResponse.json | 793 | 14ede53c2e51b289fb3c80903292d4b0f0b387eae217dbb257c201b2b7c65bf1 |
| v1\InitializeParams.json | 1864 | 4f576f99e285beb28f71f48a72b887c1f517dada86fee348fe2af0a35511de23 |
| v1\InitializeResponse.json | 1286 | 86dcd236d0576a82c85b933586dc45731260eab1b6edb3447b03f790277322b1 |
| v2\AccountLoginCompletedNotification.json | 391 | c8de16ae68ab990cc93bea4a75b08079fff3b49978f5bf3ddafe84d578d1c635 |
| v2\AccountRateLimitsUpdatedNotification.json | 4163 | 18667a04b74ed14480d4d7f6fdb412b47a4ccb25ae7d1d3b89b53120a18dddea |
| v2\AccountUpdatedNotification.json | 2423 | eaa70450768bd8204f5000f9efb46d2c244f94fff2f94f2dc4f4d8b1b0380bc0 |
| v2\AgentMessageDeltaNotification.json | 406 | 6446e05718f69c5bbbdb7da92997ffddbc307c13c1222581708f7e5ce1c9e924 |
| v2\AppListUpdatedNotification.json | 5815 | 52633897b4c7ea8e77ae77b50aa46634af110da9102b7bf5bb84bd30fd7a163c |
| v2\AppsListParams.json | 904 | 8acf7e21e1e7bd61fbcab715c2ab53df45abb36c9f6b1f664b5c2910611b4ce8 |
| v2\AppsListResponse.json | 6000 | a15c7dac1858bddc9f665482f658a1720a973d50684e64faadafcabb76d6fed2 |
| v2\CancelLoginAccountParams.json | 221 | 1e4c2930d9cdaf4a8b02facc54ca919629217ce6aa45bae65beab674861c60f4 |
| v2\CancelLoginAccountResponse.json | 404 | 8691ac0cb3c82ec8df9f523ac8571cb99b00efa2d5531bcc13c6ef34bd7892d2 |
| v2\CommandExecOutputDeltaNotification.json | 1585 | ad1457966ba7e18f9a9cd6d0abcc0e2884685c6f369f13c35d90687e1e13cb37 |
| v2\CommandExecParams.json | 7397 | 9f6c382e9f494c133952b828bd08e02cf091ff3490719a0c2c5eea35705dcbc8 |
| v2\CommandExecResizeParams.json | 1216 | 08c3b6490caa9a76a39f3fbaab8255e13adfc228033b0a1ab01dbd60f3c7a54f |
| v2\CommandExecResizeResponse.json | 188 | bc16449a418538843e3f8ec485ce19481ea066952c38764e34dd981eda0c776c |
| v2\CommandExecResponse.json | 699 | 0dbcacaa27d794d801e701513a8cfb9d247cf20624c9a05bb6231866ebeeecb8 |
| v2\CommandExecTerminateParams.json | 404 | 524a323fa2aece3c64325770d52fd18720adfa226bca93bedd59d827bdb7f74d |
| v2\CommandExecTerminateResponse.json | 194 | 7d59565735c556579512b9480aa8813de45d8bdfeaf9310a99ed59c13e04dab4 |
| v2\CommandExecutionOutputDeltaNotification.json | 416 | e9d233bc133d3fea6022fe7032cfeedad53d20d49da771a3fd3424603d6f0db1 |
| v2\CommandExecWriteParams.json | 716 | 0e60400ac3332c1bd2aef10d49e2487b5a50c40d40ad9ebc2b2f59ccb18ca7b9 |
| v2\CommandExecWriteResponse.json | 186 | 0000c7901037e77c508002f290f693a09113cfcb4370ae6159a25a4c0be61815 |
| v2\ConfigBatchWriteParams.json | 1203 | be7e07c4ec7a12f964e4ea992e1bc1252909cd96d2b809aa4bd139f17b5c78d9 |
| v2\ConfigReadParams.json | 495 | 9a51989dc089ac297037c6f1fdc65a73f9c8105837e93a998f173a80c7880172 |
| v2\ConfigReadResponse.json | 21502 | 6891153c46366e5e0dbb08c618689f56d804482140c54079c1998bca3c97ccbc |
| v2\ConfigRequirementsReadResponse.json | 13735 | cc014e3d2fd31d6ba497276d9e0ff4243d656bd79a520e9eb4a949ec8f9ca8eb |
| v2\ConfigValueWriteParams.json | 763 | 7e89f7018ff6c7cfb58b3bf16c133fc110bc0e0d9135b8e806ff64bb7ccefb1e |
| v2\ConfigWarningNotification.json | 1640 | 19308411367d17251e97f213458216882bb44c3aa342662b6a545c0e1c836965 |
| v2\ConfigWriteResponse.json | 7913 | b21d63578d3ef4a0c11042c4387b139f281c0796299ec4639d78f27d46c8fc0d |
| v2\ConsumeAccountRateLimitResetCreditParams.json | 594 | 873aabf31c21eb310e6a1447eff342d3d6d120388c5c51091768f6a8f7cdff70 |
| v2\ConsumeAccountRateLimitResetCreditResponse.json | 1177 | 1b4a2dba3ee75970a66195ce8355cccb99f3fe3489c726721463ae75c085ffe6 |
| v2\ContextCompactedNotification.json | 362 | e0b92779009971631d385970cc0389d4f19e351466f84806df36c29f4b9d5ffd |
| v2\DeprecationNoticeNotification.json | 457 | f7a487fdbfdafb3b3a882a74aa41a99ff4f1d3a69389807b6f3318540f223684 |
| v2\ErrorNotification.json | 5424 | 1ec871b02771300a26a34e41a7cfaf7484330a8c37c197d1ac133e753b083a09 |
| v2\ExperimentalFeatureEnablementSetParams.json | 509 | 84d7424cdd02f857c6c8c699a9b2ce6b107163235ea076805e5e68c12e7d8283 |
| v2\ExperimentalFeatureEnablementSetResponse.json | 386 | 806b13a0e11bb7840cc04b1b806eee98e9f7f1b3463cec320d075fade7e07b90 |
| v2\ExperimentalFeatureListParams.json | 827 | 001f41456e219e483de64f8620ab05f2822cf44cf7d3e1ac39adb2f0ec039490 |
| v2\ExperimentalFeatureListResponse.json | 3066 | 6b894c2a092dac26f4108f14152569277e1401e44b946c94f43cb55901f01cec |
| v2\ExternalAgentConfigDetectParams.json | 505 | d76038a706582bb19dc9bc5d19616646919c8dd1305fc34147abbd36024a6b22 |
| v2\ExternalAgentConfigDetectResponse.json | 4300 | 00fd0f32ae1e45db8cecccb8fb91f92691476c6971fab688ee911d36b13e936b |
| v2\ExternalAgentConfigImportCompletedNotification.json | 2672 | 3dee1102daeee6b6734177b9c2ae7c760500e5b312e38ec76afbd0431fb43261 |
| v2\ExternalAgentConfigImportHistoriesReadResponse.json | 2656 | 5da5df9afc138cd8e7bd0ee61156b86d020a4597910604c96aef86656927b210 |
| v2\ExternalAgentConfigImportParams.json | 4497 | 83a3758599f5698e53f79fb47f121ca1610cbe788e1595aed99462bea3642666 |
| v2\ExternalAgentConfigImportProgressNotification.json | 2671 | 3cf0f8868790fc5d18270c96da451903005893429a7bd776434c29b8e3719477 |
| v2\ExternalAgentConfigImportResponse.json | 232 | 668d9cabd7ada5e0135e15df5d029d5ba642ccc6aae974c86dbfce11d3cfee04 |
| v2\FeedbackUploadParams.json | 726 | e46898acf3bb3326c74ce2c6442b6ebcf052447db96c8f3d740edf901b7c265e |
| v2\FeedbackUploadResponse.json | 221 | b643e47a774302f708ba6629f7571e9b9ace68abeb7516ab0123da9a497b7c4a |
| v2\FileChangeOutputDeltaNotification.json | 544 | c99f43238e156716f4982fc4393faeb7a9b5342ca63374fe215b9d99f2219e49 |
| v2\FileChangePatchUpdatedNotification.json | 2159 | cdfef70e910f028b9afe11538e93c7f744142c819dded0028a4da4695a7f39c7 |
| v2\FsChangedNotification.json | 1061 | 6b3bd3d000f41513d4d252fb792e5450b8f1ee60db14bb1385dd696d81273492 |
| v2\FsCopyParams.json | 1198 | 3d5ad669c4a149a20033626432a915b885f5bc0c2dfdbb962be0960da073e9ff |
| v2\FsCopyResponse.json | 162 | df42cb70d864ccae345cec48e7454234d7e904fdaee35c5dbba523d3251b159b |
| v2\FsCreateDirectoryParams.json | 1047 | a4d5f69f2536b74c9204fe3caf6b17731f380ed4b26c83cd245b74c453a0a6b4 |
| v2\FsCreateDirectoryResponse.json | 184 | 8ca90dc2b3f1e3923bbee1a1a582f3d6126a86562a5bf4506c403d11bfdea5dc |
| v2\FsGetMetadataParams.json | 851 | 27e0e3ad13a718a928de453c0dbb97928dd667c11be7858adcffafaa95ae7f45 |
| v2\FsGetMetadataResponse.json | 1005 | 0b6c65e501e1572d4d6193a658d27c55f0f25475193286d5e673149dbd57ee73 |
| v2\FsReadDirectoryParams.json | 862 | 1c80ce39ca0bc2b300ed3dcccf2a51493aa1d8d27f2d30dbb0b6ef99a4a0f904 |
| v2\FsReadDirectoryResponse.json | 1139 | 9b7009479142b1ead2031b2df4c3effa50469efc30708db0aa3bdaba4d574137 |
| v2\FsReadFileParams.json | 844 | 7378f2bb4ad8fe2e12fe63a4972299eb70ca258dd2c8f553aa0baf540f96f21b |
| v2\FsReadFileResponse.json | 354 | 4e89ccdca494a9acce354225c21faab4e78a8e8685e198b4a9779ca0ab9aebb5 |
| v2\FsRemoveParams.json | 1199 | 0714e695dbaf7d5cb89622c49119023f702b52e87d0e4f8d81c089ff051b3714 |
| v2\FsRemoveResponse.json | 166 | df87770e3ae6d4816cfd6e291a892d87f64f599a9ba43167b162a4fa83e42a94 |
| v2\FsUnwatchParams.json | 368 | 77c0d06e92deb217e94db5bc3fb49649c42a50e27d22c81cbf7bb7a80ea8c8ec |
| v2\FsUnwatchResponse.json | 168 | d2243dffb042d67ccd8f96792d5740bc1339003f57e943445ef22b1e22f3692b |
| v2\FsWatchParams.json | 1042 | 49a9690c22e1eaa996f7bbbeefa6a1778196ebc625afbfd7e451331f1975e79c |
| v2\FsWatchResponse.json | 864 | 19b4f715108d7e5953200aa0c887138c20498e113a64c9b38863cee2e31c5e04 |
| v2\FsWriteFileParams.json | 970 | e85ab203c25b29e14ee210108326851433ea35817fc438475aeed40cf81e77f4 |
| v2\FsWriteFileResponse.json | 172 | 3a946ef3ac33632489f9df1826c9cca670b2dc70184225684372911d2a34a9bd |
| v2\GetAccountParams.json | 485 | d0dc3c0d428d37b1ec7887fa6b044244e6474578e1ff397c0872b2a6292073db |
| v2\GetAccountRateLimitsResponse.json | 6890 | 26bed5356b0b7abd8f3f5bdd2f55d16123e340b038afee26a35d5470f4c17f5b |
| v2\GetAccountResponse.json | 2519 | f681035a67d7779fa90a43c38b2deffaa230a32cfe10de87729b67ed97555621 |
| v2\GetAccountTokenUsageResponse.json | 1589 | b4cb098e35c43a7e3b8c9211c3c1b12a79e5d89afdcf40304e6b777e25a8c74b |
| v2\GetWorkspaceMessagesResponse.json | 1565 | 158650c7c3e8909cfb5a02a0bcc0ee765455fd33c212817b3ae64225c7873991 |
| v2\GuardianWarningNotification.json | 423 | 6ef51562302dbe0878d3c31ce773675b061952518072b93b17a4637e7930579b |
| v2\HookCompletedNotification.json | 4225 | 109a6d7ffb2534f524c0f1aceb1f54036e2dd9afd928e94441767a7dda78b8f0 |
| v2\HooksListParams.json | 311 | 653408f1430a8b7844835d676c7be8e15e601fcb859818268af87306c6b822cb |
| v2\HooksListResponse.json | 4211 | 8d556e8266f0da180467a7cfe20552e68687db6f66caa5b167ebf617685d80b6 |
| v2\HookStartedNotification.json | 4223 | 79eaec69d829877c402cf8c3d26f26219c06f1c71973deff6ebf2eb3c368d2c2 |
| v2\ItemCompletedNotification.json | 37422 | 2905f1169b0990af2b114358b0b14ddb071fdd2643ac7e43a62c8db40643abcf |
| v2\ItemGuardianApprovalReviewCompletedNotification.json | 15639 | bebe7d92fa7889300ab546db2aac21736a183ee18205da2d474642cd27288237 |
| v2\ItemGuardianApprovalReviewStartedNotification.json | 15141 | ea7b32acbc7b35359d6a9580b44c3134e17b433769c6fd8b9d4c5651afea646c |
| v2\ItemStartedNotification.json | 37414 | 0d842417a83335153e12e8f511a401b467fbc4308035f94b503bb0b5647079e2 |
| v2\ListMcpServerStatusParams.json | 1030 | 701916a7d444afbbc68aef9e72ab4e5c3111a8fd97560072e9b84713adf9ddc0 |
| v2\ListMcpServerStatusResponse.json | 4876 | 9f57363d187ee9581e88cc2fe8ed85e4b692353ae96b616689e934702442af4a |
| v2\LoginAccountParams.json | 2942 | 2f460b3f0def877a2aaa7d8e5fb84db1136f8737fb63007231992a73601a38c3 |
| v2\LoginAccountResponse.json | 2174 | 511432a4ea31cdd585ced7e7c2a573f2f2b6306269e9e36da9114b20537e933c |
| v2\LogoutAccountResponse.json | 114 | ac14ddfca2ec6092b0039be8720262d5a9930484d23e62544d2ef0b7e1db7926 |
| v2\MarketplaceAddParams.json | 431 | 0010e544bcaf05835264d30d9c51f8753ff6d0600078923bc7895d375b56de09 |
| v2\MarketplaceAddResponse.json | 865 | 016e57c26a6f4cb6eeb853c877dd613921f8affe05cc49ff6ac80563f3d37665 |
| v2\MarketplaceRemoveParams.json | 236 | 62683dd94b7f04cb0ff9139f0fd65a4054c550e1365276fc4cba689fca53d0a2 |
| v2\MarketplaceRemoveResponse.json | 869 | dd80fbf34262773465cf667a1b29ee147dafcf717ad0b4056c4f65f45baae223 |
| v2\MarketplaceUpgradeParams.json | 228 | 8b422d89afa78a06648a3f6e755b6d1205aa389adebe074c7a1ce9f7dac772a9 |
| v2\MarketplaceUpgradeResponse.json | 1348 | f1cadefec92a2538c7fa8fe15d3fb60b5d76d1bdd39da1119d899f3933d1654f |
| v2\McpResourceReadParams.json | 352 | 1b08de843ea1d95b58c5823d7f74d17d6434c7cbc29cba32b605ea3cd67d60dd |
| v2\McpResourceReadResponse.json | 1482 | afc28bec6078e86f4c3abd08bb4b07faeda47a6b95d0271f32af8d293c28f648 |
| v2\McpServerOauthLoginCompletedNotification.json | 455 | 917f416509d0a48d67ccc014cd30a6c7d21a5656ded1011af11206266171cba6 |
| v2\McpServerOauthLoginParams.json | 539 | d811d68d681d0310d71697d58ca8c65b0f2e36e856aacd510ce7f88bd2781998 |
| v2\McpServerOauthLoginResponse.json | 242 | 7a8369f66ca874851a82752b4d6579763cc1ffb44bdf1a37268ee15ab4eadc6b |
| v2\McpServerRefreshResponse.json | 117 | 54a77812db02175dc69053870e582d3b314af6f161f0c76846f3563b0f9487c4 |
| v2\McpServerStatusUpdatedNotification.json | 973 | bfd84af7be62e5f4093a904e2e290f924eb8639119557c01a34b98a1c4680be2 |
| v2\McpServerToolCallParams.json | 380 | 35fa0fddcdee23ceb24a39b1e6de62d1594b804b453dda66cc522005393f6477 |
| v2\McpServerToolCallResponse.json | 374 | 83d100ccf933b4a1ce657bc7532a79c783dcde8b4d059a2f2cc985d51538ad88 |
| v2\McpToolCallProgressNotification.json | 412 | 53db836cfa93fecef8968d3b750b936a679d5befed987c72ad6f0bc6e327f60f |
| v2\ModelListParams.json | 688 | 10ac5c0cb8ddbe840527c8eb8053dc4ced32ea3efdfb200283cadc43447f9073 |
| v2\ModelListResponse.json | 4944 | 59039842824f56f4fbdf0379b54a940986fca63e1f4577493d2498005dcbafe9 |
| v2\ModelProviderCapabilitiesReadParams.json | 128 | 9db732188a15476cabb42cf05e495ae0803a1ada993fca7348c21c30a998388d |
| v2\ModelProviderCapabilitiesReadResponse.json | 395 | e5e93e7d50e0f7c2c640ffe4d53107b97e36943680504184932fcfab4c132e71 |
| v2\ModelReroutedNotification.json | 636 | 56fafba427da6c01ac5b27be97386edcede595ae2ee83273ade01ef9931c6ff4 |
| v2\ModelSafetyBufferingUpdatedNotification.json | 746 | b4e77abcee6de1c88b3e2408240f0376ba34da81d1ee2183499b8bd13e785c65 |
| v2\ModelVerificationNotification.json | 574 | 6f099d71955d14b8b48083c7dea35f55da3f09a6a5290acbe9374d74a3fa80fd |
| v2\PermissionProfileListParams.json | 667 | 10d5550ceb091060105e0615ad10462917044bceda09f4c0816fe0eec12c5aa1 |
| v2\PermissionProfileListResponse.json | 1174 | c4ccf5f00f326e13a0886ccde89bad06dc961e118e26229289a6ced166018808 |
| v2\PlanDeltaNotification.json | 565 | 200327ed4d6f14c0f39dee5b57b7275559bea7a5db3815c4151c0e35dfac8a4c |
| v2\PluginInstalledParams.json | 1174 | 9cef9342214956f0df8a5087e2bb2aec0fdea72f841b56ad96dc2bfa51160c51 |
| v2\PluginInstalledResponse.json | 14260 | 0340d18a7f27121ea5054723858fd901cca0d699be122065b1f1cc0692f42533 |
| v2\PluginInstallParams.json | 950 | 25cb92881ca3cb8ac26c4604357c11365cbdeb00767dd3227b15a109bf904a30 |
| v2\PluginInstallResponse.json | 1190 | 92aed7fa5993040d7d2430c7c3b38795ddde5de0626e468d7a0801bb7d7426ed |
| v2\PluginListParams.json | 1487 | dbed97096d7312100cf5d8da6c60d2dd8d17fce4dcc5e0594d9475e9d8269fad |
| v2\PluginListResponse.json | 14383 | 5f5d21f61aaa27e5cb50dc8f0bb7d77c0162ce0adcdf272a1eb6fa906cb218ea |
| v2\PluginReadParams.json | 947 | df191e956fb0905d4133ec1436962d786b6d3a02ac98d7f856827d4ed6018af6 |
| v2\PluginReadResponse.json | 18521 | 873fce37fbfacd73af87ebab816510a6ac491927d88611ca1a75e6f46439016e |
| v2\PluginShareCheckoutParams.json | 236 | 6dcf85fefecee8ccfa9349115c3cb63f27b110c30f76e2afe5b6c59819f16c94 |
| v2\PluginShareCheckoutResponse.json | 1187 | 4ae360ca0bf382cfe88e8baca70a7b5e700a3d9414cb3925c44812de1430997c |
| v2\PluginShareDeleteParams.json | 234 | a106ba3c2731c9733e83d2afa16bfeff29bde9c9b6c7589a9126aa61d34f291c |
| v2\PluginShareDeleteResponse.json | 118 | ae881a7f898f5287354de5adf6f42094cef9029a9b9376bfd12aa1b0fe0b3106 |
| v2\PluginShareListParams.json | 114 | 0d71280d2a655ffc26375044ec1693203848614f99fdb4563e0cb5a4ee40122b |
| v2\PluginShareListResponse.json | 13058 | 145997b5a0a59911cfc20fa3f15f3fa0e915e88c2d28de1b8e195fe9233b1045 |
| v2\PluginShareSaveParams.json | 1974 | e7f71b6333a5067a143305fb0acd0ba6babc3ed5578bde5a6bed896aefb801f9 |
| v2\PluginShareSaveResponse.json | 298 | 50241c6132ada20f175c9cd1bedad0c9d82c6f8685a7845a2f517f2978b8e439 |
| v2\PluginShareUpdateTargetsParams.json | 1351 | 9f208ca281b781d7060c16ddaa3062b82af44899906704c15adc4a648fe42e18 |
| v2\PluginShareUpdateTargetsResponse.json | 1380 | 233b8d8f34eef31d0ee5dc86110028651e9e74e899332204c0871c8f8820cbe5 |
| v2\PluginSkillReadParams.json | 388 | 7496330dfdf494d658f5dc6998fef1fc30d312616f036b08cf11d61fce23a20b |
| v2\PluginSkillReadResponse.json | 220 | ec7edbbcc67ee6d4d5fcc707d152b9ebb3b1e8fe3e27bee035094a2b806bc600 |
| v2\PluginUninstallParams.json | 220 | 15de77dd5ab132305fa57299c41ea911a0ca22c4ca960be5bb5506378ae5d291 |
| v2\PluginUninstallResponse.json | 116 | f6a0c9c8dfd9e3fc4a535ecfa606f12922c09e469a37d6b1c6e189f45cacb89e |
| v2\ProcessExitedNotification.json | 1419 | 32bc785470cf22ed93f50d508b96061b557feaf824ca3fd9f27ddd40b13581ef |
| v2\ProcessOutputDeltaNotification.json | 1436 | 71d9279da816715d741e1b315cde8737ca7016d0827b552e796a481d2eb70cb8 |
| v2\RawResponseItemCompletedNotification.json | 29862 | d301779bf7b5866a5ec68e0bbca82bb870ea8ba9345462765de1a71342a2252b |
| v2\ReasoningSummaryPartAddedNotification.json | 454 | 5cc137e90e8d51d703a671578245d2d983f6fe063d113e8f6c6af0279011253c |
| v2\ReasoningSummaryTextDeltaNotification.json | 512 | 7bd24d5d1cad5b09f6d3fe7a40de6a2d0c3218678bad6199e704c4696597325b |
| v2\ReasoningTextDeltaNotification.json | 505 | e2e7aea35c4d95b4ad8eeafe15629113f69ebfc2be085cd4316ae02871b5b110 |
| v2\RemoteControlStatusChangedNotification.json | 800 | a091c7b37eca746e09e9dc714883807f58c6ce48a13e5c7badd67c103a880ca2 |
| v2\ReviewStartParams.json | 3200 | 2b0e0ec6d41e91e155339a646a1f1124146e879d40ada6a02e374736129c1497 |
| v2\ReviewStartResponse.json | 44965 | 9c7fbfd79763d9af65505992f7a9e84d6a430feac3ff22cc54dbe8adac1a314b |
| v2\SendAddCreditsNudgeEmailParams.json | 420 | e69a23583e5decc5e9688413c1db8efac775d067e6714fc9211cffdea7c6be8b |
| v2\SendAddCreditsNudgeEmailResponse.json | 417 | 7c5fcdd7a08408b41f1d2d04761bc3e60ec1d8010e63a612cc42efaff968c26d |
| v2\ServerRequestResolvedNotification.json | 514 | 7cc32a834be009c7a45dda01b80ae01bb522c642eb0e886c6d4bd7ee89fde643 |
| v2\SkillsChangedNotification.json | 341 | d9d3bda6c7fa1ed2bafc2a6145676760972c0220dee6b1d7229f82a25297a1f3 |
| v2\SkillsConfigWriteParams.json | 1011 | 24c9645b4f09b3d4d6ed8a18dda989717959e200eb3a70cd455a97e0c3754ca1 |
| v2\SkillsConfigWriteResponse.json | 241 | fe29c58a71cb9ab1048602970bcbe8059facab90879fdf7607d3c3158f21ee34 |
| v2\SkillsExtraRootsSetParams.json | 761 | 4e5f1864e142df3db1c607b60279b3214e41a4c5bd13c81211cb8a580107ca65 |
| v2\SkillsExtraRootsSetResponse.json | 120 | b659ec17a196eaa3a23273a3e2d5596460ea329bc3ade249971576bc723d9e96 |
| v2\SkillsListParams.json | 453 | a942aa92e6da4cf8a76d6b99cbdaf6672864e6ff955f44a6d469f0363afd3bf2 |
| v2\SkillsListResponse.json | 4732 | 9d7dcc8cc75aac6be70803a941eedc9594725b66eacaa54062a3b6518acf70d5 |
| v2\TerminalInteractionNotification.json | 474 | fc4d4d076e3fe220108ce2f606a29148ab2e87ca7300de00a469656e424c234b |
| v2\ThreadApproveGuardianDeniedActionParams.json | 360 | d544a1253790a2b29d6de8cbce3065d6a238518e3af8d964cfd8a2b18eecd99e |
| v2\ThreadApproveGuardianDeniedActionResponse.json | 134 | 628336c7cdabd508d5202695502bb0f9ce42fdde380f5efdd4b033cf215d93e5 |
| v2\ThreadArchivedNotification.json | 225 | 3a166dd21245bf260f16d2fd55da81ba57d9143c629881fba32638b53351c18d |
| v2\ThreadArchiveParams.json | 218 | c02dd30ee0f272ec74c36f1e5cd44a5d9a4f0ceb5ba089b26a758e1375a320ca |
| v2\ThreadArchiveResponse.json | 114 | 2e021dabd0930ae3e2beb68cd0ddbafc40fc20cfbf8dbcb5e2f9c0493d06a08c |
| v2\ThreadClosedNotification.json | 223 | d9978b8a5450dc6ba01cac5ae6641493aa60c558340a718722a78eda990048c8 |
| v2\ThreadCompactStartParams.json | 223 | a7c4395f60bddd38c953cb9508a05d2082790da7b3eb4ef7e4fb66cf15b92227 |
| v2\ThreadCompactStartResponse.json | 119 | 74674e47f33e37d7d97d144eac1789ef0580a1fc12d45a5675bb97caa08d803a |
| v2\ThreadDeletedNotification.json | 224 | f9706a810ea40567dbcf59b1d926059fa9b13b7f2d85a97e1dcb388ee0a2edc5 |
| v2\ThreadDeleteParams.json | 217 | b0c68afa98bf529010fe643da11db29a8a0a2f2963c8e7a25831f8612f98297d |
| v2\ThreadDeleteResponse.json | 113 | 333c0c5df96d78d1bcbdb19b57ddfa941de9a02bdae1c29d13c5311c2cdf6527 |
| v2\ThreadForkParams.json | 4995 | 7d915a57492f5cb7abbf323d12b6dafa7a4ab0bd403079ebe4be0457aa9223ba |
| v2\ThreadForkResponse.json | 61835 | f3ce10b3a2f9af0ee6758e34c0ef8cc0cc92f140d465763a11b644a0ed3ffa07 |
| v2\ThreadGoalClearedNotification.json | 228 | 58b6a04cae193932eb9e02858dfed26469d8d7b0a648cf3ee1a8ad0adc1ff515 |
| v2\ThreadGoalClearParams.json | 220 | 3283dabc2860c3abdb59a73061aaf290b486bff3afdf9c46cdeccceeb0f0fa3c |
| v2\ThreadGoalClearResponse.json | 221 | 2f8ca66feaaaec3d3d0144ce23c65702c764ba92c4820dd3a6b84f7635c1655f |
| v2\ThreadGoalGetParams.json | 218 | 619172ae68da00476e628c515f44c212af2d913af77066266e5347809eb19023 |
| v2\ThreadGoalGetResponse.json | 1491 | 8bf8015d45a3dbf02ffd02a90c524aabe2916334bea435e59fad0498d64c4845 |
| v2\ThreadGoalSetParams.json | 804 | 6216508b6a7ba9640be7861b2fb14e8719100468ea906fc65dd882701cb86423 |
| v2\ThreadGoalSetResponse.json | 1428 | be5a571f10afdae3ad5a61db0891fa8a66001c1d5ab618163a131efa461061ac |
| v2\ThreadGoalUpdatedNotification.json | 1580 | bbecea6da1be75429c5c1dffd1a8307223ae97d861a96cb66af6ae34d901140d |
| v2\ThreadInjectItemsParams.json | 397 | c2a14a9d3d66e54e1672b440824676a8292200a39ea765615cbfa98b07862fb0 |
| v2\ThreadInjectItemsResponse.json | 118 | 93d909e5b49b44da02072d340f8ad0ecd417d448ccf27fe50be7e369c7864159 |
| v2\ThreadListParams.json | 3359 | ed58445a1033a1c57184e6ee389ab5904e42fbcada6febcb692172d293f30853 |
| v2\ThreadListResponse.json | 55435 | 9cc28ca2aa488ddfd38d88cce06db6baa7fec47cc9cf88529428cf129bfd418f |
| v2\ThreadLoadedListParams.json | 489 | f1c2e4944f64ea6361dc4681f948d0c6fe2e79304ae2eb36ddac06a3258d3885 |
| v2\ThreadLoadedListResponse.json | 565 | 681bacf8a22e64427f2de8b67a2d3dba925e68f35ca5556a96a4c9fbc8eca85c |
| v2\ThreadMetadataUpdateParams.json | 1465 | 7bb606e803920bbe05ab8375804f73bd5b9cb7cce0dadf8b91616052e63eddbb |
| v2\ThreadMetadataUpdateResponse.json | 54781 | 9c6661adf47b3866bc44bf304499c671961e2aa1906795842480131446ac893b |
| v2\ThreadNameUpdatedNotification.json | 312 | 7cd55307c0343508b226b9fe87cb7af09711fb438cdeb5eb440cf37a5d2f5000 |
| v2\ThreadReadParams.json | 355 | db97080f82facc3259dbb9404e9f0df81e360619f4cd73983a9d99d25f5089ee |
| v2\ThreadReadResponse.json | 54771 | 86adff6de376b567306c20ccb23ec4684c427324cf8ae96f2016e31fdc58023e |
| v2\ThreadRealtimeClosedNotification.json | 393 | d02cde1cf11584913474e80576175d448be81553b445769540728879c147be43 |
| v2\ThreadRealtimeErrorNotification.json | 377 | 56a748c0a4b092a4ca065ffb48ff94e5d7bf07a73ef5b0494e61750f718a68ec |
| v2\ThreadRealtimeItemAddedNotification.json | 358 | 58b1962daa93465e0ceb6a901b94fecabd4345e59329ebd830367b9293d150f6 |
| v2\ThreadRealtimeOutputAudioDeltaNotification.json | 1261 | 688b323b533e78befa8b61a24e5ce9363c722048ea8e2903d0115e289f27417d |
| v2\ThreadRealtimeSdpNotification.json | 376 | c6650c43b28006844897466905ea24e58fa4021a76cdb0dcd46b88547507b049 |
| v2\ThreadRealtimeStartedNotification.json | 647 | 5268db503ee5b8ad0f40956cbb119cd84fed35f801e09b799afc81c653301cbb |
| v2\ThreadRealtimeTranscriptDeltaNotification.json | 533 | c117b16578c2712397a37f2604e2ea393de2e31af135f4c4ab639ff275217ed5 |
| v2\ThreadRealtimeTranscriptDoneNotification.json | 528 | a242b3528f8fae45830c9b1ff65037bd240a327c5f7b2212fd42b3819c02d4ca |
| v2\ThreadResumeParams.json | 36128 | 0b87ae0bf1d05d6984bdb1bd306b42493de303584c86c7600b09f45c18022c5d |
| v2\ThreadResumeResponse.json | 62311 | 5ecc84d9fd4dd40e3fac783330130a2a3c616f6bfa6d92cf0c95d5e53f94da5e |
| v2\ThreadRollbackParams.json | 667 | 27a2e5c31d48fa682a40b17a5abfef82428ed3a08cb265daff4b214956f63ba6 |
| v2\ThreadRollbackResponse.json | 55102 | c5139703ff64ed4c7e574c4d587a3c37ebbbd5dbb989a896dd673c766568d868 |
| v2\ThreadSetNameParams.json | 274 | ff608965de4fcfb7241dd0afead85af3b71a5d116cb7900a0839239c1fa7ed9f |
| v2\ThreadSetNameResponse.json | 114 | f8763716433feea94f7c1ebd59b6cea4c261432e83240c13a8d49ba8185aa567 |
| v2\ThreadSettingsUpdatedNotification.json | 10318 | 6e74137db80611a24d7ea4a90d533d5e7bdba60e2b75f79041a335ea8d597f84 |
| v2\ThreadShellCommandParams.json | 567 | f6eab1894a695d5415727ca68477d0bf3e428fc96a8cb6e80482fe5ba0fd2124 |
| v2\ThreadShellCommandResponse.json | 119 | fd4911613f6a6da2ca13b75342501d4d61f64e00a1083c6d60038369b8caeefe |
| v2\ThreadStartedNotification.json | 54778 | 20b28c251399a660388abd03600593d680ea12add5e075c5a06366a0db419880 |
| v2\ThreadStartParams.json | 9873 | 8c676098d8abf8405cbc20f00d907f22c4b5196fce7ef31fcf9426b8939de255 |
| v2\ThreadStartResponse.json | 61836 | a075f6984d1aaba8fe1f2f439fc8e8c19d5be532b250162d694166fa3fcf1d88 |
| v2\ThreadStatusChangedNotification.json | 2207 | 146af6d3702c4f3c844bd10b6b6b3e2b872e958a8d7d822157c19aaa6dc085f6 |
| v2\ThreadTokenUsageUpdatedNotification.json | 1600 | fe70a73653ae9e3fffb0db84d1312f47ac47d92526c2d44461492cd864ada3ad |
| v2\ThreadUnarchivedNotification.json | 227 | ef4344864ff316d1749ae947368c1e617cf8825e5c8af8a828167ef991299b88 |
| v2\ThreadUnarchiveParams.json | 220 | 43505599fbc58d4b410da2da0f1bae08ad1ef0d4d7d310cb0d82c07517dd5a89 |
| v2\ThreadUnarchiveResponse.json | 54776 | 13af2f439087984eb769407e3d2f1f624033cb9bad815d34b6208ceb38e9cfa8 |
| v2\ThreadUnsubscribeParams.json | 222 | a03dc3d6c5a2f77f164b6bf4250d29f0c81c10c6b5f484fac0b05392dc9c936a |
| v2\ThreadUnsubscribeResponse.json | 431 | 14cd8baac4521c8101698de3df2d7a8e509ece6bf9b8b5d11162d9391064b4ae |
| v2\TurnCompletedNotification.json | 44772 | 900bc6e40f0aeb5aa498ec874f026a2a077356ac1dc740c1928cfdca29630345 |
| v2\TurnDiffUpdatedNotification.json | 494 | 100de58cee497cbf2382497d0c0215761e6c5baa872009a0a55ff7d23364635c |
| v2\TurnInterruptParams.json | 278 | 49132b57b09f09dc545ed1cd373c12eede6e880e9afb54ae50add78bb42490cd |
| v2\TurnInterruptResponse.json | 114 | 531de6be06fe979b5963f249bab82498a175e614bf65ac12fb2e849dfe60bcf1 |
| v2\TurnModerationMetadataNotification.json | 331 | 3e2aa78ccd487d22d2c6a12b275d9c6fbc8be707e24d59d44aad05a75107dbd3 |
| v2\TurnPlanUpdatedNotification.json | 948 | 1198bbc36eebf79716b97ab7a36c09e77bec76efccefa1f188fe2e22db26d196 |
| v2\TurnStartedNotification.json | 44770 | 8fa9297e89172a4430b8a023dc6fe6f4b5764578fb64df0d41886be25e64e669 |
| v2\TurnStartParams.json | 15396 | de310c74b51c41564f767440f91ff4fe780728904022e2882ebda49f128ec188 |
| v2\TurnStartResponse.json | 44700 | 7cfae42a4652fe38119d6a0a625910357c869c448c985513a4cd5966031e18bc |
| v2\TurnSteerParams.json | 5482 | a8c722611743b757a83949e3c90a0f09fb436f66e8d96ec2f364be0340c51531 |
| v2\TurnSteerResponse.json | 212 | a669b85e3b75b86468e39e6a2f760966bffe83f378b514c6898fb04b838cd78d |
| v2\WarningNotification.json | 454 | 8f8f87048f846a24ce06937cbcc11098e188ff262b71a986c0bcde1a24e57a81 |
| v2\WindowsSandboxReadinessResponse.json | 435 | 002d645f1c0967ac8a4b1a0954e2c7561f8e299bd62dfe494026ae377d64c79e |
| v2\WindowsSandboxSetupCompletedNotification.json | 556 | 158145a7dda28e654796a06e1b627e6315283c3c0f2389b9502cf1c658ce8188 |
| v2\WindowsSandboxSetupStartParams.json | 1002 | 35b4aa0a40a160ce84b44e821d78096e37712c2f8824ec374ad5be564f35fbe2 |
| v2\WindowsSandboxSetupStartResponse.json | 230 | 29a24d86a75d5c76d5ae82fc8a3136fe2f0bc5785f5bfd033a0abc05834090d3 |
| v2\WindowsWorldWritableWarningNotification.json | 478 | 17181244dfd86ece4375ad483ec6bcc6795443d85a2bf23b6f7e42fbcf1fa7e1 |

## Safety assertions

- No --experimental flag was used.
- No login was initiated.
- No actual Turn was run.
- No persistent app-server was started.
- Toolchain Codex processes remaining at report generation: 0.
- No Houdini probe or P1 work was performed.
- No new Git commit was created during P0-C2A-R1.

Report generated from verified local artifacts at 2026-07-14T01:00:07+08:00.
