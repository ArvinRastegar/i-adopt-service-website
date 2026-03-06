# stream-to-string

Convert a stream to a string

[![GitHub license](https://img.shields.io/github/license/jeswr/stream-to-string.svg)](https://github.com/jeswr/stream-to-string/blob/master/LICENSE)
[![npm version](https://img.shields.io/npm/v/@jeswr/stream-to-string.svg)](https://www.npmjs.com/package/@jeswr/stream-to-string)
[![build](https://img.shields.io/github/actions/workflow/status/jeswr/stream-to-string/nodejs.yml?branch=main)](https://github.com/jeswr/stream-to-string/tree/main/)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![semantic-release](https://img.shields.io/badge/%20%20%F0%9F%93%A6%F0%9F%9A%80-semantic--release-e10079.svg)](https://github.com/semantic-release/semantic-release)

## Usage

```ts
import { Readable } from 'readable-stream';
import stringify from '@jeswr/stream-to-string';

// 'abc'
await stringify(Readable.from(['a', 'b', 'c']));
```

## License
©2024–present
[Jesse Wright](https://github.com/jeswr),
[MIT License](https://github.com/jeswr/stream-to-string/blob/master/LICENSE).
