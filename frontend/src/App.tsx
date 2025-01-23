import React, { useState, useCallback } from 'react';
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
  Container,
  createTheme,
  ThemeProvider,
  Autocomplete,
} from '@mui/material';
import FinancialChatbox from './components/FinancialChatbox';
import { debounce } from 'lodash';
import DownloadIcon from '@mui/icons-material/Download';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend } from 'chart.js';
import { Bar } from 'react-chartjs-2';

// Register ChartJS components
ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
);

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8080'

const theme = createTheme({
  typography: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, sans-serif',
  },
});
const App: React.FC = () => {
  const [ticker, setTicker] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [financialData, setFinancialData] = useState<Record<ReportType, FinancialData | null>>({
    income_statement: null,
    balance_sheet: null,
    cash_flow: null
  });
  const [searchResults, setSearchResults] = useState<Array<{
    symbol: string;
    name: string;
  }>>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const debouncedSearch = useCallback(
    debounce((query: string) => {
      searchSymbols(query);
    }, 300),
    []
  );

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

  const searchSymbols = async (query: string) => {
    if (!query || query.length < 1) {
      setSearchResults([]);
      return;
    }

    setSearchLoading(true);
    try {
      const response = await fetch(
        `https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords=${query}&apikey=${process.env.REACT_APP_ALPHA_VANTAGE_API_KEY}`
      );
      const data = await response.json();
      
      if (data.bestMatches) {
        setSearchResults(
          data.bestMatches.map((match: any) => ({
            symbol: match['1. symbol'],
            name: match['2. name'],
          }))
        );
      }
    } catch (err) {
      console.error('Failed to fetch symbols:', err);
    } finally {
      setSearchLoading(false);
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
                    align={index === 0 ? 'left' : 'right'}
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
                      align={colIndex === 0 ? 'left' : 'right'}
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

  const renderFinancialChart = (data: FinancialData | null) => {
    if (!data) return null;

    // Find revenue and gross profit rows
    const revenueRow = data.data.find(row => {
      const metric = row[data.columns[0]];
      return typeof metric === 'string' && 
        metric.toLowerCase().includes('revenue') && 
        !metric.toLowerCase().includes('cost');
    });
    const grossProfitRow = data.data.find(row => {
      const metric = row[data.columns[0]];
      return typeof metric === 'string' && 
        metric.toLowerCase().includes('gross profit');
    });

    if (!revenueRow || !grossProfitRow) return null;

    const years = data.columns.slice(1);
    
    const chartData = {
      labels: years,
      datasets: [
        {
          label: 'Revenue',
          data: years.map(year => parseFloat(revenueRow[year].toString().replace(/[^0-9.-]+/g, ''))),
          backgroundColor: 'rgba(53, 162, 235, 0.5)',
          borderColor: 'rgba(53, 162, 235, 1)',
          borderWidth: 1,
        },
        {
          label: 'Gross Profit',
          data: years.map(year => parseFloat(grossProfitRow[year].toString().replace(/[^0-9.-]+/g, ''))),
          backgroundColor: 'rgba(75, 192, 192, 0.5)',
          borderColor: 'rgba(75, 192, 192, 1)',
          borderWidth: 1,
        },
      ],
    };

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'top' as const,
        },
        title: {
          display: true,
          text: 'Revenue and Gross Profit Trends',
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            callback: function(value: any, index: number, values: any[]): string {
              if (typeof value !== 'number') return '';
              return value >= 1e9 
                ? `$${(value / 1e9).toFixed(1)}B`
                : value >= 1e6
                ? `$${(value / 1e6).toFixed(1)}M`
                : `$${value}`;
            },
          },
        },
      },
    };

    return (
      <Box sx={{ height: 400, mt: 4 }}>
        <Bar data={chartData} options={options} />
      </Box>
    );
  };

  return (
    <ThemeProvider theme={theme}>
      <Container maxWidth="xl" sx={{ py: 4 }}>
        <Box sx={{ 
          display: 'flex', 
          alignItems: 'center', 
          mb: 4,
          gap: 2,
          width: '100%'
        }}>
          <Box
            component="img"
            src="/stonkie.png" 
            alt="Stonkie logo" 
            sx={{ 
              height: '60px',
              '@media (max-width: 600px)': {
                height: '40px'
              }
            }}
          />
          <Box component="form" onSubmit={handleSubmit} sx={{ flexGrow: 1 }}>
            <Box sx={{ display: 'flex', gap: 2 }}>
              <Autocomplete
                value={ticker}
                onChange={(_, newValue) => setTicker(typeof newValue === 'string' ? newValue : newValue?.symbol || '')}
                onInputChange={(_, newInputValue, reason) => {
                  if (reason === 'input') {
                    debouncedSearch(newInputValue);
                  }
                }}
                options={searchResults}
                getOptionLabel={(option) => 
                  typeof option === 'string' 
                    ? option 
                    : `${option.symbol} - ${option.name}`
                }
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder="Enter stock ticker (e.g., AAPL)"
                    label="Stock Ticker"
                    variant="outlined"
                    size="small"
                    sx={{ 
                      '& .MuiOutlinedInput-root': {
                        borderRadius: '12px'
                      }
                    }}
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {searchLoading && (
                            <CircularProgress color="inherit" size={20} />
                          )}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                  />
                )}
                freeSolo
                sx={{ flexGrow: 1 }}
                loading={searchLoading}
              />
              <Button
                type="submit"
                variant="contained"
                disabled={loading || !ticker.trim()}
                sx={{ 
                  minWidth: 120,
                  borderRadius: '12px',
                  '@media (max-width: 600px)': {
                    minWidth: 42,
                  }
                }}
              >
                {loading ? (
                  <CircularProgress size={24} color="inherit" />
                ) : (
                  <DownloadIcon />
                )}
              </Button>
            </Box>
          </Box>
        </Box>

        {error && (
          <Alert severity="error" sx={{ mb: 4 }}>
            {error}
          </Alert>
        )}

        {renderTable(financialData.income_statement, 'Income Statement')}
        {renderFinancialChart(financialData.income_statement)}

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
          maxWidth: '500px',
          width: '100%',
        }}
      >
        <FinancialChatbox 
          ticker={ticker} 
          initialMessage="Hi! My name is Stonkie, your stock agent. Feel free to ask me anything about a particular stock you are interested in."
        />
      </Box>
    </ThemeProvider>
  );
};

export default App; 