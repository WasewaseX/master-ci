using System.Collections.Generic;

namespace Warehouse.Jobs
{
    public sealed class CommandRunner
    {
        private readonly Queue<string>? commands;

        public string Next()
        {
            var next = commands!.Dequeue();
            return next;
        }
    }
}
