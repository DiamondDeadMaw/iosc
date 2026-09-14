# iosc

A Windows native iOS toolchain for *personal use*. Compile Swift, link, bundle, sign, and install onto an iPhone entirely without a mac.


## Setting up

Prereqs:

- Python 3.11 or newer. Get it here: <https://www.python.org/downloads/>
- The windows swift toolchain, which gives us the compiler: <https://www.swift.org/install/windows/>
- Apple Mobile Device Support, required to install (building signing and packaging work without) the app to a phone. Get it here: https://apps.microsoft.com/detail/9np83lwlpz9k?hl=en-US&gl=IN. Alternatively, iTunes also works. 
- MinGW-w64, for `g++` on your PATH. Builds the ADI native bridge used for free Apple ID signing. Get it here: <https://www.mingw-w64.org/downloads/>
- Visual Studio Build Tools, with the C++ workload and the Windows SDK. Builds the Swift macro plugins that make SwiftUI and SwiftData work. Get it here: <https://visualstudio.microsoft.com/downloads/> (you do not need full Visual Studio)

Build the exe:

    Set-ExecutionPolicy Bypass -Scope Process -Force; .\packaging\build.ps1

That creates `dist\iosc.exe`. Add that to your PATH, or move the exe wherever you want. Then run:

    iosc toolchain status

The command will tell you what is setup, and whats missing. Follow its instructions to install the rest of the prereqs.


# Quick Start
1. Complete `iosc toolchain status` to download dependencies. It will guide you.
2. `iosc auth login <apple_id>`. Will ask you for your password, then the code sent to your iPhone
3. Run `iosc init` to get your Sources/ directory. Build your project there. 
4. `iosc build` to create the compiled app. 
5. `iosc sign --apple-id <apple_id>` Will sign the app with your certificate.
6. `iosc install` will install the app on the connected iPhone. 


### Location of external files

Everything you are asked to move lies in an `external` directory. iosc
picks one of these, in order:

1. `%IOSC_EXTERNAL%`, if you set it.
2. `external` beside `iosc.exe`, if that directory exists.
3. `%LOCALAPPDATA%\iosc\external` otherwise.

Note- external needs to be in the same directory as iosc.exe. Either put your files next to wherever the exe is, or set `%IOSC_EXTERNAL%`.

`iosc toolchain status` prints which of the three it picked, and `iosc dev paths` prints the absolute path decided on for every asset.

### Linking a system library

Apple SDK frameworks go in the `frameworks` list in `iosc.toml`, by their exact name. 
System libraries like SQLite are passed to the linker. Create a `linker_flags` list and pass whatever the linker would expect:

    linker_flags = ["-lsqlite3"]

### Reading file:line in a crash report

`iosc build` writes a `.dSYM` next to the built app. Point `iosc crash symbolicate` at
it with `--dsym`, alongside `--binary` for the app:

    iosc crash symbolicate report.ips --binary build/Demo.app/Demo --dsym build/Demo.dSYM

That resolves each frame to a source file and line.

## Commands

Every command also takes `--project <dir>` (defaults to the current directory),
`--verbose`, and `--json`.

    iosc init                      write an iosc.toml for the current project
        --name <name>                  app name, defaults to the project folder name
        --bundle-id <id>               reverse DNS bundle id, defaults to com.example.<name>

    iosc init creates an empty Sources/ directory next to iosc.toml. Put your .swift files there before building

    iosc sdk extract <xip>          unpack required SDK out of a .xip
        --no-resume                    do not resume a partial extraction, start over

    iosc sdk status                 report whether the SDK is present and usable

    iosc sdk verify                 check the extracted SDK against its expected layout

    iosc auth login <apple_id>      sign in with an Apple ID to get a signing identity
                                     (will then prompt you for your password, and the code
                                     sent to your device)
        --anisette <source>            where to get anisette data from, default "local"
                                        local     generate it on this machine from external files placed during setup
                                        <url>     fetch it from a remote anisette server
                                        remote    same as a url, passed separately
                                        file      read it from a captured file
                                        static    use a fixed, hardcoded set of headers

    iosc auth status <apple_id>     show the signing identity cached for an Apple ID

    iosc auth apk-extract [apkm]    pull ADI libraries out of the Apple Music apkm in
                                     external/apk (or the given path), needed once before
                                     'iosc auth login --anisette local' will work

    iosc toolchain fetch            download the linker and debugging tools into external/tools
        --force                        re-download even if already present

    iosc toolchain status           report what is set up and what is missing

    iosc build                      compile and link into an unsigned .app
        --force                        rebuild even if outputs look up to date

    iosc sign                       code sign the built .app for a device or profile
        --certificate <path>           signing certificate to use
        --key <path>                   private key matching the certificate
        --profile <path>               provisioning profile to embed
        --udid <udid>                  device to sign for
        --apple-id <id>                Apple ID to derive a signing identity from
        --team-id <id>                 team id to use when the Apple ID has more than one
        --revoke-and-reissue           revoke the existing certificate and get a new one
        --force                        sign even if a valid signature already exists

    iosc package                    bundle the signed .app into an installable .ipa

    iosc devices                    list iOS devices attached over USB

    iosc install                    install the built app onto a device
        --udid <udid>                  device to install onto, default the first attached one

    iosc log                        stream the device syslog
        --udid <udid>                  device to stream from
        --contains <text>              only show lines containing this text
        --os-log                       read from os_log instead of the plain syslog
        --pid <pid>                    only show lines from this process id
        --process-name <name>          only show lines from this process name
        --match <text>                 extra filter text, repeatable

    iosc crash list                 list crash reports stored on the device
        --udid <udid>                  device to list from
        --remote-path <path>           directory on the device to list, default "/"
        --depth <n>                    how many directory levels to recurse, default 1

    iosc crash pull <out>           copy crash reports off the device
        --udid <udid>                  device to pull from
        --remote-path <path>           directory on the device to pull from, default "/"
        --match <text>                 only pull reports whose name contains this
        --erase                        delete the reports off the device after pulling

    iosc crash symbolicate <report> resolve addresses in a crash report to source
        --binary <path>                binary to resolve symbols against, repeatable
        --dsym <path>                  a .dSYM bundle to also resolve file:line from, repeatable
        --thread <n>                   only symbolicate this thread



    iosc clean                      remove build output for the current project

    iosc dev <probe>                inspect internal state; probes are sdk, toolchain,
                                     device, paths, tunnel

## Planned commands:
    iosc launch                     start an installed app on the device
            --udid <udid>                  device to launch on
            --env <KEY=VALUE>              environment variable to set, repeatable
            --no-kill-existing             do not kill a running instance first
            --suspended                    launch stopped, waiting for a debugger
            -- <args>                      everything after -- is passed to the app untouched
        For now just tap it yourself

    iosc run                        build, sign, install, and optionally launch, in one step (TODO)
                                     (takes every flag `iosc sign` takes, plus)
        --no-log                       do not stream the log after install
        --launch                       also launch the app after install

Run `iosc --help` or `iosc <command> --help` for the same details.

## What you need to bring

Proprietary software is brought by you. By defualt, this is assumed to be placed inside external/.  

### `external/xcode`

Extracted from an Xcode `.xip` you download from Apple with your own developer account.

    iosc sdk extract path\to\Xcode.xip
    iosc sdk status

### `external/apple_certs`

Two public certificates, published by Apple on their Certificate Authority page at
<https://www.apple.com/certificateauthority/>. Download them yourself and place both in
`external/apple_certs`.
Apppend the file name to the link to direct download. 
FIles: `AppleWWDRCAG3.cer`, `AppleIncRootCertificate.cer`

### `external/tools`

A different `ld64.lld` is required. iosc will fetch this automatically from llvm's release. 

    iosc toolchain fetch
    iosc toolchain status

### `external/apk`

Only needed for `iosc auth login --anisette local` (the default). Download an Apple
Music v6.5.2 `.apkm`, move it to this path, then run:

    iosc auth apk-extract
    iosc toolchain status


# Capabilities and Limitations
## Capabilities
- SwiftUI and SwiftData _mostly_ work. 38/47 swift macros have been reimplemented. Unimplemented: EntryDefaultValueMacro, AnimatableValuesDataMacro, AnimatablePairDataMacro, AnimatableValuesDataPropertyMacro, AnimatablePairDataPropertyMacro, SessionPropertyEntryDefaultValueMacro, Previewable, RuleMacro. Most of these are internal and unreachable. 
- UIKit apps via storyboards and xibs. Need to write xml by hand, sorry.
- Asset catalogs
- Code signing w/ a paid dev account, or a free one. 
- Packaging to a .app/ .ipa
- Device install
- Syslog streaming
- Crash reports

## Limitations
- No ide, visual editor, swiftUI canvas, or preview. 
- No debugger
- No simulator (yet)
- No Swift package manager (next item on the todo list)
- No XCTest equivalent/support
- No core data- you will need to use swift data instead. 
- No App Intents (@AppEntity is not complete as of now). So no shortcuts or siri.
- No app store or test flight submission. You should probably get a Mac if you want to publish your app.
- No extensions, watchOS, macOS, etc. No widgets
- No metal shaders. 
- I have only tested the following macros (on an iPhone): @State, @Model, @Query, @Relationship, @Attribute, @Transient, #Unique, #Index, @ModelActor, @Entry, @Animatable, @AnimatableIgnored, @Parameter
- These macros compile, but are untested: @Bindable, @Observable, @Environment, @AppStorage, @SceneStorage, @FocusState, @Namespace, @Preview. Use them at your peril. I will get around to this. 

## Development

`.\iosc.cmd` runs without building anything (for local testing). Its first
run makes a `.venv` beside it and installs iosc into that. It reads `external` from this directory rather than from `%LOCALAPPDATA%`.

## Credits

**Bundled with `dist\iosc.exe`**

- [LZFSE](https://github.com/lzfse/lzfse) - Copyright (c) 2015-2016, Apple Inc. All rights reserved. BSD 3-Clause. Used to decompress the SDK payload during `iosc sdk extract`.

**Build and runtime dependencies**

- [swift-syntax](https://github.com/swiftlang/swift-syntax) - Apache-2.0 with Runtime Library Exception. For the macro plugins. 
- [cryptography](https://github.com/pyca/cryptography) - Apache-2.0 / BSD-3-Clause. RSA keygen and X.509 for device pair records.
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) - GPL-3.0. Optional. Needed for crash report retrieval and os_log.
- [LLVM](https://llvm.org) - Apache-2.0 with LLVM Exception. `ld64.lld`, `dsymutil` and `llvm-symbolizer`, fetched by `iosc toolchain fetch`.

**You install these yourself**

- [Swift toolchain for Windows](https://www.swift.org/install/windows/) - Apache-2.0 with Runtime Library Exception.
- [Python](https://www.python.org/) - PSF License.
- Apple Mobile Device Support, or iTunes - proprietary, Apple. Provides usbmuxd for device communication.

**You supply these**

- The iOS SDK, extracted from an Xcode `.xip` you download with your own Apple developer account.
- Apple's public CA certificates, from <https://www.apple.com/certificateauthority/>.
- The ADI libraries, extracted from an Apple Music `.apkm` you download yourself.


## Contributing

Contributions are accepted under the [Developer Certificate of Origin](https://developercertificate.org/). Sign your commits with `git commit -s`. That line certifies you wrote the change, or otherwise have the right to submit it under this license.

## License

Copyright (C) 2026 DiamondDeadMaw (github.com/DiamondDeadMaw/iosc)

iosc is free software under the [GNU Affero General Public License v3.0](LICENSE). You can use it, read it, change it, and pass it on. If you distribute a modified version, or run one as a network service, you have to give people the source under the same terms. It stays free for everyone, always.

This license covers iosc itself. It claims nothing over the apps you build with it. Anything you compile, sign, or package using iosc is entirely yours, commercially or otherwise.