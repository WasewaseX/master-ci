#include <iostream>

void HandleJob(int job)
{
    if (job < 0)
    {
        std::cerr << "rejecting negative job\n";
    }
}
