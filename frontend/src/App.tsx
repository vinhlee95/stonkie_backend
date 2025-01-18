import React, { useState } from 'react';
import { FinancialData, ReportType } from './types';
import { formatNumber } from './utils/formatters';
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  TextField,
  Button,
  Typography,
  Alert,
  Box,
  CircularProgress,
  Container
} from '@mui/material';
import FinancialChatbox from './components/FinancialChatbox';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8080'

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
        `${BACKEND_URL}/api/financial-data/${ticker.toLowerCase()}/${reportType}`
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

    const reportTypes: ReportType[] = ['income_statement', 'balance_sheet', 'cash_flow'];
    await Promise.all(reportTypes.map(type => fetchFinancialData(type)));
  };

  const renderTable = (data: FinancialData | null, title: string) => {
    if (!data) return null;

    const formatCellValue = (value: any, column: string): string => {
      // Don't format the first column (usually metric names)
      if (column === data.columns[0]) return value;
      return formatNumber(value);
    };

    return (
      <Box sx={{ mt: 4 }}>
        <Typography variant="h5" sx={{ mb: 2 }}>
          {title}
        </Typography>
        <Typography variant="body1" sx={{ mb: 2 }}>
          All numbers are in thousands of USD.
        </Typography>
        <TableContainer component={Paper} sx={{ maxHeight: 440 }}>
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                {data.columns.map((column, index) => (
                  <TableCell
                    key={index}
                    sx={{
                      fontWeight: 'bold',
                      backgroundColor: 'background.paper'
                    }}
                  >
                    {column}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {data.data.map((row, rowIndex) => (
                <TableRow
                  key={rowIndex}
                  sx={{ '&:nth-of-type(odd)': { backgroundColor: 'action.hover' } }}
                >
                  {data.columns.map((column, colIndex) => (
                    <TableCell 
                      key={colIndex}
                      align={colIndex === 0 ? 'left' : 'right'} // Right-align numbers
                    >
                      {formatCellValue(row[column], column)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>
    );
  };

  return (
    <>
      <Container maxWidth="xl" sx={{ py: 4 }}>
        <Typography variant="h3" component="h1" sx={{ mb: 4 }}>
          Stock agent 🚀
        </Typography>
        <Typography variant="body1" sx={{ mb: 4 }}>
          Invest easier and wiser every day 💰
        </Typography>
        
        <Box component="form" onSubmit={handleSubmit} sx={{ mb: 4 }}>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="Enter stock ticker (e.g., AAPL)"
              label="Stock Ticker"
              variant="outlined"
              size="small"
              sx={{ flexGrow: 1 }}
            />
            <Button
              type="submit"
              variant="contained"
              disabled={loading || !ticker.trim()}
              sx={{ minWidth: 120 }}
            >
              {loading ? (
                <CircularProgress size={24} color="inherit" />
              ) : (
                'Fetch Data'
              )}
            </Button>
          </Box>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 4 }}>
            {error}
          </Alert>
        )}

        {renderTable(financialData.income_statement, 'Income Statement')}
        {renderTable(financialData.balance_sheet, 'Balance Sheet')}
        {renderTable(financialData.cash_flow, 'Cash Flow Statement')}
      </Container>

      <Box
        sx={{
          position: 'fixed',
          bottom: 20,
          right: 20,
          zIndex: 1000,
          boxShadow: 20,
          maxWidth: '400px',
          width: '100%'
        }}
      >
        {ticker && <FinancialChatbox ticker={ticker} />}
      </Box>
    </>
  );
};

export default App; 