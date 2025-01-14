export interface FinancialData {
  data: Record<string, string | number>[];
  columns: string[];
}

export interface FinancialResponse {
  data: FinancialData;
  error?: string;
}

export type ReportType = 'income_statement' | 'balance_sheet' | 'cash_flow'; 