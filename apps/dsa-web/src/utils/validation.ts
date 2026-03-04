interface ValidationResult {
  valid: boolean;
  message?: string;
  normalized: string;
}

// 校验单个股票代码
const SINGLE_PATTERNS = [
  /^\d{6}$/, // A 股 6 位数字
  /^(SH|SZ)\d{6}$/, // A 股带交易所前缀
  /^\d{5}$/, // 港股 5 位数字
  /^[A-Z]{1,6}(\.[A-Z]{1,2})?$/, // 美股常见 Ticker
];

const isValidSingle = (code: string): boolean =>
  SINGLE_PATTERNS.some((regex) => regex.test(code));

// 兼容 A/H/美股常见代码格式的基础校验，支持逗号/空格分隔多只
export const validateStockCode = (value: string): ValidationResult => {
  const normalized = value.trim().toUpperCase();

  if (!normalized) {
    return { valid: false, message: '请输入股票代码', normalized };
  }

  // 按逗号、空格、中文逗号分隔
  const codes = normalized.split(/[,，\s]+/).filter(Boolean);

  const invalid = codes.filter((c) => !isValidSingle(c));
  if (invalid.length > 0) {
    return {
      valid: false,
      message: `股票代码格式不正确: ${invalid.join(', ')}`,
      normalized: codes.join(','),
    };
  }

  return {
    valid: true,
    message: undefined,
    normalized: codes.join(','),
  };
};
