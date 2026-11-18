using System;
using System.IO;

namespace Warehouse.Intake
{
    public sealed class DocumentGate
    {
        public string Load(string key)
        {
            try
            {
                try
                {
                    return File.ReadAllText(key);
                }
                catch (IOException)
                {
                    throw;
                }
            }
            catch (Exception)
            {
                throw;
            }
        }
    }
}
