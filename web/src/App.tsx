import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { RunList } from './routes/RunList';
import { RunView } from './routes/RunView';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/runs" replace />} />
        <Route path="/runs" element={<RunList />} />
        <Route path="/runs/:id" element={<RunView />} />
      </Routes>
    </BrowserRouter>
  );
}
