using System;
using System.IO;

namespace Warehouse.Audit
{
    public sealed class LedgerSweeper
    {
        public bool Sweep(string path)
        {
            try
            {
                return File.ReadAllLines(path).Length > 0;
            }
            catch (IOException ex)
            {
                Console.Error.WriteLine("sweep skipped: " + ex.Message);
                return false;
            }
        }
    }
}
