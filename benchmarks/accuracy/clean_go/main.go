// Package main ships a tiny shelf-report CLI: read sku/quantity pairs
// from stdin, print the shelf report sorted by sku.
package main

import (
	"bufio"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
)

func parseLine(line string) (string, int, error) {
	parts := strings.Fields(line)
	if len(parts) != 2 {
		return "", 0, fmt.Errorf("want '<sku> <quantity>', got %q", line)
	}
	quantity, err := strconv.Atoi(parts[1])
	if err != nil {
		return "", 0, fmt.Errorf("quantity %q is not a number", parts[1])
	}
	return parts[0], quantity, nil
}

func report(shelves map[string]int) []string {
	skus := make([]string, 0, len(shelves))
	for sku := range shelves {
		skus = append(skus, sku)
	}
	sort.Strings(skus)
	lines := make([]string, 0, len(skus))
	for _, sku := range skus {
		lines = append(lines, fmt.Sprintf("%-12s x%d", sku, shelves[sku]))
	}
	return lines
}

func main() {
	shelves := make(map[string]int)
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		sku, quantity, err := parseLine(line)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		shelves[sku] = quantity
	}
	for _, line := range report(shelves) {
		fmt.Println(line)
	}
}
