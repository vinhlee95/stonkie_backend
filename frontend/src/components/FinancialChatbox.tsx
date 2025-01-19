import React, { useState } from 'react';
import { Box, TextField, Button, Paper, Typography, CircularProgress } from '@mui/material';
import ChatBubbleIcon from '@mui/icons-material/ChatBubble';
import ReactMarkdown from 'react-markdown';
import { useTheme } from '@mui/material/styles';

interface Message {
  type: 'user' | 'bot';
  content: string;
}

interface FinancialChatboxProps {
  ticker: string;  // Current ticker passed from parent component
  initialMessage: string;  // Add this new prop
}

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8080'

const FinancialChatbox: React.FC<FinancialChatboxProps> = ({ ticker, initialMessage }) => {
  const [messages, setMessages] = useState<Message[]>(() => [
    // Initialize messages with the welcome message
    { type: 'bot', content: initialMessage }
  ]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const theme = useTheme();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;

    // Add user message to chat
    const userMessage: Message = { type: 'user', content: input };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    try {
      const response = await fetch(`${BACKEND_URL}/api/company/analyze`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: input, ticker: ticker }),
      });

      if (!response.ok) {
        throw new Error('Failed to get analysis');
      }

      const responseData = await response.json();
      
      // Add bot response to chat - now accessing nested data structure
      const botMessage: Message = { 
        type: 'bot', 
        content: responseData.data.data || responseData.data 
      };
      setMessages(prev => [...prev, botMessage]);
    } catch (error) {
      // Add error message to chat
      const errorMessage: Message = { 
        type: 'bot', 
        content: 'Sorry, I encountered an error analyzing the data.' 
      };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
      setInput('');
    }
  };

  const MessageContent: React.FC<{ content: string, isUser: boolean }> = ({ content, isUser }) => {
    if (isUser) {
      return <Typography>{content}</Typography>;
    }
    
    return (
      <ReactMarkdown
        components={{
          h2: ({ children }) => (
            <Typography variant="h6" sx={{ fontWeight: 'bold', mt: 1, mb: 2 }}>
              {children}
            </Typography>
          ),
          p: ({ children }) => (
            <Typography sx={{ mb: 1.5 }}>{children}</Typography>
          ),
          strong: ({ children }) => (
            <Typography component="span" sx={{ fontWeight: 'bold' }}>
              {children}
            </Typography>
          ),
          ul: ({ children }) => (
            <Box component="ul" sx={{ pl: 2, mb: 1.5 }}>
              {children}
            </Box>
          ),
          li: ({ children }) => (
            <Typography component="li" sx={{ mb: 0.5 }}>
              {children}
            </Typography>
          ),
          table: ({ children }) => (
            <Box sx={{ overflowX: 'auto', mb: 2 }}>
              <table style={{ 
                borderCollapse: 'collapse', 
                width: '100%',
                fontSize: '0.875rem'
              }}>
                {children}
              </table>
            </Box>
          ),
          th: ({ children }) => (
            <th style={{ 
              border: '1px solid #ddd',
              padding: '8px',
              backgroundColor: '#f5f5f5',
              textAlign: 'left'
            }}>
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td style={{ 
              border: '1px solid #ddd',
              padding: '8px'
            }}>
              {children}
            </td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    );
  };

  return (
    <Box sx={{ position: 'fixed', bottom: 20, right: 20, maxWidth: '90%', width: 600 }}>
      {!isVisible && (
        <Button 
          onClick={() => setIsVisible(true)} 
          variant="contained" 
          sx={{ 
            minWidth: 'auto',
            width: 56,
            height: 56,
            borderRadius: '50%',
            float: 'right'
          }}
        >
          <ChatBubbleIcon />
        </Button>
      )}
      
      {isVisible && (
        <Paper elevation={3} sx={{ 
          p: 2, 
          clear: 'both', 
          position: 'relative',
          borderRadius: 4  // More rounded corners for main chat window
        }}>
          {/* Header Section */}
          <Box sx={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            borderBottom: 1,
            borderColor: 'divider',
            pb: 1,
            mb: 2
          }}>
            <Typography variant="h6" sx={{ 
              display: 'flex', 
              alignItems: 'center',
              gap: 1
            }}>
              Chat with Stonkie
            </Typography>
            <Button 
              onClick={() => setIsVisible(false)}
              sx={{ 
                minWidth: 'auto',
                width: 40,
                height: 40,
                p: 0,
                borderRadius: '50%',
                '&:hover': {
                  backgroundColor: 'rgba(0, 0, 0, 0.04)'
                }
              }}
            >
              <Typography sx={{ fontSize: '24px' }}>✕</Typography>
            </Button>
          </Box>
          
          <Box sx={{ 
            height: '75vh',
            overflowY: 'auto',
            mt: 2,
            '&::-webkit-scrollbar': {
              width: '8px',
            },
            '&::-webkit-scrollbar-track': {
              background: '#f1f1f1',
            },
            '&::-webkit-scrollbar-thumb': {
              background: '#888',
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-thumb:hover': {
              background: '#555',
            },
          }}>
            {messages.map((message, index) => (
              <Box
                key={index}
                sx={{
                  display: 'flex',
                  justifyContent: message.type === 'user' ? 'flex-end' : 'flex-start',
                  mb: 2,
                  position: 'relative',
                  alignItems: 'flex-start',
                }}
              >
                {message.type === 'bot' && (
                  <Box
                    component="img"
                    src="/stonkie.png"
                    alt="AI Avatar"
                    sx={{
                      width: 40,
                      height: 40,
                      borderRadius: '50%',
                      mr: 1,
                      flexShrink: 0,
                    }}
                  />
                )}
                <Paper
                  sx={{
                    p: 2,
                    maxWidth: message.type === 'user' ? '70%' : '85%',
                    bgcolor: message.type === 'user' ? 'primary.light' : 'grey.50',
                    color: message.type === 'user' ? 'white' : 'text.primary',
                    borderRadius: 2,
                    boxShadow: 2,
                    position: 'relative',
                    '&::before': message.type === 'bot' ? {
                      content: '""',
                      position: 'absolute',
                      width: 0,
                      height: 0,
                      borderStyle: 'solid',
                      left: -10,
                      borderWidth: '10px 10px 10px 0',
                      borderColor: 'transparent #f5f5f5 transparent transparent',
                      top: 10,
                    } : {}
                  }}
                >
                  <MessageContent 
                    content={message.content} 
                    isUser={message.type === 'user'} 
                  />
                </Paper>
              </Box>
            ))}
            {isLoading && (
              <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
                <CircularProgress size={24} />
              </Box>
            )}
          </Box>

          <form onSubmit={handleSubmit}>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <TextField
                fullWidth
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about financial analysis..."
                disabled={isLoading}
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 3  // More rounded corners for text input
                  }
                }}
              />
              <Button 
                type="submit" 
                variant="contained" 
                disabled={isLoading || !input.trim()}
                sx={{
                  borderRadius: 3  // More rounded corners for send button
                }}
              >
                Send
              </Button>
            </Box>
          </form>
        </Paper>
      )}
    </Box>
  );
};

export default FinancialChatbox; 