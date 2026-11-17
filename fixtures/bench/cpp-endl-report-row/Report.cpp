#include <fstream>

void WriteRow(std::ofstream& report, const char* row)
{
    report << row << std::endl;
}
