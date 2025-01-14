import React, { useState } from 'react';
import { FinancialData, ReportType } from './types';

const App: React.FC = () => {
  const [ticker, setTicker] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [financialData, setFinancialData] = useState<Record<ReportType, FinancialData | null>>({
    income_statement: null,
    balance_sheet: null,
    cash_flow: null
  });

  const fetchFinancialData = async (reportType: ReportType) => {
    try {
      setLoading(true);
      setError(null);
      
      const response = await fetch(
        `http://localhost:8000/api/financial-data/${ticker.toLowerCase()}/${reportType}`
      );
      
      if (!response.ok) {
        throw new Error(`Failed to fetch ${reportType} data`);
      }

      const data = await response.json();
      setFinancialData(prev => ({
        ...prev,
        [reportType]: data
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker.trim()) return;

    // Fetch all report types
    const reportTypes: ReportType[] = ['income_statement', 'balance_sheet', 'cash_flow'];
    await Promise.all(reportTypes.map(type => fetchFinancialData(type)));
  };

  const renderTable = (data: FinancialData | null, title: string) => {
    if (!data) return null;

    return (
      <div className="mt-8">
        <h2 className="text-xl font-bold mb-4">{title}</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full bg-white border border-gray-300">
            <thead>
              <tr>
                {data.columns.map((column, index) => (
                  <th key={index} className="px-4 py-2 border-b border-gray-300 bg-gray-100">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.data.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {data.columns.map((column, colIndex) => (
                    <td key={colIndex} className="px-4 py-2 border-b border-gray-300">
                      {row[column]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="container mx-auto px-4 py-8">
      <h1 className="text-3xl font-bold mb-8">Financial Statement Analysis</h1>
      
      <form onSubmit={handleSubmit} className="mb-8">
        <div className="flex gap-4">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="Enter stock ticker (e.g., AAPL)"
            className="flex-1 p-2 border border-gray-300 rounded"
          />
          <button
            type="submit"
            disabled={loading || !ticker.trim()}
            className="px-4 py-2 bg-blue-500 text-white rounded disabled:bg-gray-300"
          >
            {loading ? 'Loading...' : 'Fetch Data'}
          </button>
        </div>
      </form>

      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      {renderTable(financialData.income_statement, 'Income Statement')}
      {renderTable(financialData.balance_sheet, 'Balance Sheet')}
      {renderTable(financialData.cash_flow, 'Cash Flow Statement')}
    </div>
  );
};

export default App; 