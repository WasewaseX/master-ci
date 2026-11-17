#include <fstream>

void LogEvent(std::ofstream& log, const char* event)
{
    log << event << '\n';
}
