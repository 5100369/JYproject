// Public verification key only. The private publisher key is kept outside Git.
// 私钥位置：.release-tools/update-signing/private.pem（已 gitignore），
// 同时需要配置到 GitHub 仓库 Secrets：VIDEOMATRIX_UPDATE_PRIVATE_KEY
// 注意：更换密钥后旧版本无法验证新签名，必须随新版本一起发布。
export const UPDATE_PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA/OW04ccxlR4Mc4PdlE14RpM1/mkyFreHrkBbyioDZcI=
-----END PUBLIC KEY-----`
