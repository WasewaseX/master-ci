using System;
using System.IO;

namespace Warehouse.Intake
{
    public sealed class LedgerLoader
    {
        public string Load(string path)
        {
            try
            {
                return File.ReadAllText(path);
            }
            catch (IOException)
            {
                throw;
            }
        }
    }
}
